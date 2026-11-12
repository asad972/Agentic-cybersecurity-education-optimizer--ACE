"""Dataset replayer that publishes records to Kafka in event-time order.

Reads one or more JSON Lines files, sorts valid records globally by event date,
paces delivery according to simulated event time, validates and routes every
message through ``ace.contracts``, and persists checkpoints to Cassandra's
``replay_position`` table only after delivery is confirmed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from ace import contracts

logger = logging.getLogger(__name__)

DEFAULT_SECONDS_PER_DAY = 5.0


def resolve_seconds_per_day(seconds_per_day: float | None = None) -> float:
    """Return pacing factor; explicit argument wins over REPLAY_SECONDS_PER_DAY."""
    if seconds_per_day is not None:
        return float(seconds_per_day)
    env_val = os.environ.get("REPLAY_SECONDS_PER_DAY", "").strip()
    if env_val:
        return float(env_val)
    return DEFAULT_SECONDS_PER_DAY


def compute_sleep_seconds(
    prev_date: date | None,
    curr_date: date,
    seconds_per_day: float,
) -> float:
    """Compute real-world sleep duration between two consecutive event dates."""
    if prev_date is None or curr_date <= prev_date or seconds_per_day <= 0:
        return 0.0
    return (curr_date - prev_date).days * seconds_per_day


def _stable_record_id(record: dict[str, Any]) -> str:
    """Return a deterministic identifier for tie-breaking and safe resume."""
    for key in ("event_id", "id", "job_id"):
        val = record.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _row_get(row: Any, field: str) -> Any:
    """Read a column value from either a mapping or a Cassandra row object."""
    if isinstance(row, dict):
        return row.get(field)
    return getattr(row, field, None)


@dataclass(frozen=True)
class ReplayCheckpoint:
    """Saved progress state for resuming a dataset replay after a restart."""

    dataset: str
    row_number: int
    event_date: date
    last_event_id: str | None = None
    updated_at: datetime | None = None


class ReplayPositionStore(Protocol):
    """Storage interface for reading and persisting dataset replay checkpoints."""

    def load(self, dataset: str) -> ReplayCheckpoint | None:
        """Load the last persisted checkpoint for ``dataset``, if any."""

    def save(self, checkpoint: ReplayCheckpoint) -> None:
        """Persist ``checkpoint`` after Kafka delivery has been confirmed."""


class InMemoryPositionStore:
    """In-memory checkpoint store for local runs and unit tests."""

    def __init__(self) -> None:
        self._positions: dict[str, ReplayCheckpoint] = {}

    def load(self, dataset: str) -> ReplayCheckpoint | None:
        return self._positions.get(dataset)

    def save(self, checkpoint: ReplayCheckpoint) -> None:
        self._positions[checkpoint.dataset] = checkpoint


class CassandraPositionStore:
    """Persists replay progress to Cassandra's ``replay_position`` table."""

    def __init__(self, session: Any, keyspace: str | None = None) -> None:
        self._session = session
        self._table = f"{keyspace}.replay_position" if keyspace else "replay_position"

    def load(self, dataset: str) -> ReplayCheckpoint | None:
        query = (
            f"SELECT row_number, event_date, last_event_id, updated_at "
            f"FROM {self._table} WHERE dataset = %s"
        )
        rows = list(self._session.execute(query, (dataset,)))
        if not rows:
            return None

        row = rows[0]
        raw_date = _row_get(row, "event_date")
        if raw_date is None or str(raw_date).strip() == "":
            raise ValueError(
                f"Corrupt checkpoint in replay_position for dataset {dataset!r}: missing event_date"
            )

        if isinstance(raw_date, datetime):
            parsed_date = raw_date.date()
        elif isinstance(raw_date, date):
            parsed_date = raw_date
        else:
            try:
                parsed_date = date.fromisoformat(str(raw_date)[:10])
            except ValueError as exc:
                raise ValueError(
                    f"Corrupt checkpoint in replay_position for dataset {dataset!r}: invalid event_date {raw_date!r}"
                ) from exc

        row_number = _row_get(row, "row_number")
        raw_event_id = _row_get(row, "last_event_id")
        last_event_id = (
            str(raw_event_id).strip()
            if raw_event_id is not None and str(raw_event_id).strip()
            else None
        )
        updated_at = _row_get(row, "updated_at")

        return ReplayCheckpoint(
            dataset=dataset,
            row_number=int(row_number or 0),
            event_date=parsed_date,
            last_event_id=last_event_id,
            updated_at=updated_at,
        )

    def save(self, checkpoint: ReplayCheckpoint) -> None:
        query = (
            f"INSERT INTO {self._table} "
            f"(dataset, row_number, event_date, last_event_id, updated_at) "
            f"VALUES (%s, %s, %s, %s, %s)"
        )
        updated_at = checkpoint.updated_at or datetime.now(timezone.utc)
        self._session.execute(
            query,
            (
                checkpoint.dataset,
                int(checkpoint.row_number),
                checkpoint.event_date,
                checkpoint.last_event_id,
                updated_at,
            ),
        )


@dataclass(frozen=True)
class _PreparedRecord:
    """Internal sortable wrapper for a validated-date record."""

    event_date: date
    record_id: str
    message: dict[str, Any]


def _publish_outgoing(producer: Any, outgoing: contracts.Outgoing) -> Any:
    """Send an Outgoing contract envelope and return any delivery future."""
    if hasattr(producer, "send"):
        return producer.send(outgoing.topic, key=outgoing.key, value=outgoing.value)
    if callable(producer):
        return producer(outgoing)
    raise TypeError(
        "producer must expose .send(topic, key=..., value=...) or be callable"
    )


def _confirm_delivery(producer: Any, pending_futures: list[Any]) -> None:
    """Flush buffered messages and verify every send future succeeded."""
    if hasattr(producer, "flush"):
        flush_result = producer.flush()
        if (
            isinstance(flush_result, int)
            and not isinstance(flush_result, bool)
            and flush_result > 0
        ):
            raise RuntimeError(
                f"Kafka flush incomplete: {flush_result} message(s) remaining undelivered"
            )

    for fut in pending_futures:
        if fut is None:
            continue
        if hasattr(fut, "get") and callable(fut.get):
            fut.get(timeout=10)
        elif hasattr(fut, "result") and callable(fut.result):
            fut.result(timeout=10)
        else:
            failed_attr = getattr(fut, "failed", False)
            is_failed = failed_attr() if callable(failed_attr) else bool(failed_attr)
            if is_failed:
                raise RuntimeError("Kafka message delivery failed")

            exc_attr = getattr(fut, "exception", None)
            exc = exc_attr() if callable(exc_attr) else exc_attr
            if exc is not None:
                raise exc if isinstance(exc, BaseException) else RuntimeError(str(exc))

    pending_futures.clear()


class EventReplayer:
    """Replays static dataset files into Kafka in global event-time order."""

    def __init__(
        self,
        date_field: str = "posted_date",
        date_format: str = "%Y-%m-%d",
        seconds_per_day: float | None = None,
        producer_name: str = "ace-replay",
        position_store: ReplayPositionStore | None = None,
        cassandra_session: Any = None,
        keyspace: str | None = None,
        checkpoint_interval: int = 25,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.date_field = date_field
        self.date_format = date_format
        self.seconds_per_day = resolve_seconds_per_day(seconds_per_day)
        self.producer_name = producer_name
        self.checkpoint_interval = max(1, checkpoint_interval)
        self._sleep = sleep_fn

        if position_store is not None:
            self.position_store: ReplayPositionStore | None = position_store
        elif cassandra_session is not None:
            self.position_store = CassandraPositionStore(
                cassandra_session, keyspace=keyspace
            )
        else:
            self.position_store = None

        self.current_position = 0

    def _parse_event_date(
        self, message: dict[str, Any], raw_row: dict[str, Any]
    ) -> tuple[date | None, str | None]:
        """Parse and validate the event date; return (date, None) or (None, error_reason)."""
        raw_val = message.get(self.date_field)
        if raw_val is None:
            raw_val = raw_row.get(self.date_field)
        if raw_val is None or str(raw_val).strip() == "":
            return None, f"missing required date field {self.date_field!r}"

        date_text = str(raw_val).strip()[:10]
        try:
            parsed = datetime.strptime(date_text, self.date_format).date()
            return parsed, None
        except ValueError:
            return (
                None,
                f"invalid date {raw_val!r} in {self.date_field!r} (expected {self.date_format})",
            )

    def _route_invalid(
        self,
        topic: str,
        reason: str,
        payload: Any,
        producer: Any,
    ) -> Any:
        """Build and publish an errors.invalid record for rejected input rows."""
        invalid_msg = contracts.invalid_record(
            topic, self.producer_name, reason, payload
        )
        outgoing = contracts.Outgoing(
            topic=contracts.INVALID_TOPIC,
            key=topic,
            value=invalid_msg,
        )
        return _publish_outgoing(producer, outgoing)

    def load_and_sort(
        self,
        file_paths: str | Path | Sequence[str | Path],
        topic: str,
        producer: Any = None,
        row_to_message: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        checkpoint: ReplayCheckpoint | None = None,
    ) -> list[_PreparedRecord]:
        """Load one or more JSONL files and return valid records sorted by event time.

        Malformed JSON lines, mapping failures, and missing/unparseable dates are
        routed to ``errors.invalid`` on a fresh run (``checkpoint is None``), but
        are not re-published on a restart so invalid rows are never duplicated.
        """
        if isinstance(file_paths, (str, Path)):
            paths = [Path(file_paths)]
        else:
            paths = [Path(p) for p in file_paths]

        prepared: list[_PreparedRecord] = []
        invalid_futures: list[Any] = []
        emit_invalid = producer is not None and checkpoint is None

        for path in paths:
            if not path.is_file():
                raise FileNotFoundError(f"Dataset file not found: {path}")

            with path.open("r", encoding="utf-8") as handle:
                for line_num, raw_line in enumerate(handle, start=1):
                    stripped = raw_line.strip()
                    if not stripped:
                        continue
                    try:
                        raw_row = json.loads(stripped)
                    except json.JSONDecodeError as exc:
                        reason = f"invalid JSON in {path.name}:{line_num}: {exc}"
                        logger.warning(reason)
                        if emit_invalid:
                            invalid_futures.append(
                                self._route_invalid(topic, reason, stripped, producer)
                            )
                        continue

                    if not isinstance(raw_row, dict):
                        reason = f"expected JSON object in {path.name}:{line_num}"
                        if emit_invalid:
                            invalid_futures.append(
                                self._route_invalid(topic, reason, raw_row, producer)
                            )
                        continue

                    if row_to_message is not None:
                        try:
                            mapped = row_to_message(raw_row)
                        except Exception as exc:
                            reason = f"row mapping failed: {exc}"
                            if emit_invalid:
                                invalid_futures.append(
                                    self._route_invalid(topic, reason, raw_row, producer)
                                )
                            continue
                        if mapped is None:
                            continue
                    else:
                        mapped = raw_row

                    event_date, date_error = self._parse_event_date(mapped, raw_row)
                    if date_error is not None or event_date is None:
                        if emit_invalid:
                            invalid_futures.append(
                                self._route_invalid(
                                    topic,
                                    date_error or "invalid event date",
                                    raw_row,
                                    producer,
                                )
                            )
                        continue

                    rec_id = _stable_record_id(mapped)
                    prepared.append(
                        _PreparedRecord(
                            event_date=event_date,
                            record_id=rec_id,
                            message=mapped,
                        )
                    )

        if invalid_futures and producer is not None:
            _confirm_delivery(producer, invalid_futures)

        prepared.sort(key=lambda item: (item.event_date, item.record_id))
        return prepared

    def _should_skip(
        self,
        index_1based: int,
        item: _PreparedRecord,
        checkpoint: ReplayCheckpoint | None,
    ) -> bool:
        """Determine whether ``item`` was already published before a restart."""
        if checkpoint is None:
            return False
        if checkpoint.last_event_id is not None:
            return (item.event_date, item.record_id) <= (
                checkpoint.event_date,
                checkpoint.last_event_id,
            )
        return index_1based <= checkpoint.row_number

    def replay(
        self,
        file_paths: str | Path | Sequence[str | Path],
        topic: str,
        producer: Any,
        dataset: str | None = None,
        row_to_message: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
        on_progress: Callable[[ReplayCheckpoint], None] | None = None,
    ) -> int:
        """Replay a dataset into Kafka through ``ace.contracts.route`` with checkpointing."""
        if isinstance(file_paths, (str, Path)):
            dataset_name = dataset or Path(file_paths).stem
        else:
            paths_list = list(file_paths)
            if not paths_list:
                return 0
            dataset_name = dataset or Path(paths_list[0]).stem

        checkpoint = (
            self.position_store.load(dataset_name)
            if self.position_store is not None
            else None
        )

        prepared = self.load_and_sort(
            file_paths=file_paths,
            topic=topic,
            producer=producer,
            row_to_message=row_to_message,
            checkpoint=checkpoint,
        )

        # Start pacing from the first record emitted in this session (avoid sleeping
        # across the gap between a saved checkpoint date and the resumed record).
        prev_date: date | None = None
        published_since_save = 0
        pending_futures: list[Any] = []
        latest_checkpoint: ReplayCheckpoint | None = checkpoint
        self.current_position = checkpoint.row_number if checkpoint else 0

        for index_1based, item in enumerate(prepared, start=1):
            if self._should_skip(index_1based, item, checkpoint):
                continue

            sleep_sec = compute_sleep_seconds(
                prev_date, item.event_date, self.seconds_per_day
            )
            if sleep_sec > 0:
                self._sleep(sleep_sec)
            prev_date = item.event_date

            outgoing = contracts.route(
                topic, item.message, producer=self.producer_name
            )
            pending_futures.append(_publish_outgoing(producer, outgoing))

            self.current_position = index_1based
            published_since_save += 1
            latest_checkpoint = ReplayCheckpoint(
                dataset=dataset_name,
                row_number=index_1based,
                event_date=item.event_date,
                last_event_id=item.record_id,
                updated_at=datetime.now(timezone.utc),
            )

            if published_since_save >= self.checkpoint_interval:
                _confirm_delivery(producer, pending_futures)
                if self.position_store is not None:
                    self.position_store.save(latest_checkpoint)
                if on_progress is not None:
                    on_progress(latest_checkpoint)
                published_since_save = 0

        # Confirm delivery and persist the final position at the end of the dataset
        _confirm_delivery(producer, pending_futures)
        if published_since_save > 0 and latest_checkpoint is not None:
            if self.position_store is not None:
                self.position_store.save(latest_checkpoint)
            if on_progress is not None:
                on_progress(latest_checkpoint)

        return self.current_position


def replay_file_to_kafka(
    file_paths: str | Path | Sequence[str | Path],
    date_field: str,
    producer: Any,
    topic: str,
    **kwargs: Any,
) -> int:
    """Convenience helper forwarding to ``EventReplayer.replay``."""
    dataset = kwargs.pop("dataset", None)
    row_to_message = kwargs.pop("row_to_message", None)
    on_progress = kwargs.pop("on_progress", None)
    replayer = EventReplayer(date_field=date_field, **kwargs)
    return replayer.replay(
        file_paths=file_paths,
        topic=topic,
        producer=producer,
        dataset=dataset,
        row_to_message=row_to_message,
        on_progress=on_progress,
    )