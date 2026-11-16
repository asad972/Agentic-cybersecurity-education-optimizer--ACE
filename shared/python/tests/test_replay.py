"""Unit tests for ace.replay."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ace import contracts
from ace.replay import (
    CassandraPositionStore,
    EventReplayer,
    InMemoryPositionStore,
    ReplayCheckpoint,
    compute_sleep_seconds,
    replay_file_to_kafka,
    resolve_seconds_per_day,
)


class FakeKafkaProducer:
    """In-memory Kafka producer recording sends and flush calls."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []
        self.flushed_at_counts: list[int] = []

    def send(self, topic: str, key: str, value: dict[str, Any]) -> None:
        self.sent.append((topic, key, value))

    def flush(self) -> int:
        self.flushed_at_counts.append(len(self.sent))
        return 0

    def by_topic(self, topic: str) -> list[tuple[str, dict[str, Any]]]:
        return [(k, v) for t, k, v in self.sent if t == topic]


class FakeCassandraSession:
    """In-memory fake Cassandra session mimicking replay_position queries."""

    def __init__(self) -> None:
        self.stored: dict[str, dict[str, Any]] = {}

    def execute(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        if query.lstrip().upper().startswith("SELECT"):
            row = self.stored.get(params[0])
            return [row] if row else []
        dataset, row_number, event_date, last_event_id, updated_at = params
        self.stored[dataset] = {
            "row_number": row_number,
            "event_date": event_date,
            "last_event_id": last_event_id,
            "updated_at": updated_at,
        }
        return []


def _make_job(event_id: str, posted_date: str, country: str = "DE") -> dict[str, Any]:
    return {
        "event_id": event_id,
        "source": "synthetic",
        "country": country,
        "posted_date": posted_date,
        "job_title": f"Security Engineer {event_id[:4]}",
        "description": "SOC monitoring and SIEM triage",
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any] | str]) -> Path:
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_event_time_order_and_multi_file_sort(tmp_path: Path) -> None:
    """Records across multiple files must be sorted globally in event-time order."""
    f1 = _write_jsonl(
        tmp_path / "part1.jsonl",
        [
            _make_job("33333333-3333-3333-3333-333333333333", "2026-10-05", "DE"),
            _make_job("11111111-1111-1111-1111-111111111111", "2026-10-01", "FR"),
        ],
    )
    f2 = _write_jsonl(
        tmp_path / "part2.jsonl",
        [
            _make_job("22222222-2222-2222-2222-222222222222", "2026-10-03", "ES"),
        ],
    )

    producer = FakeKafkaProducer()
    replayer = EventReplayer(date_field="posted_date", seconds_per_day=0.0)
    count = replayer.replay([f1, f2], topic="raw.jobs", producer=producer)

    assert count == 3
    published = producer.by_topic("raw.jobs")
    assert [msg["event_id"] for _, msg in published] == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "33333333-3333-3333-3333-333333333333",
    ]
    assert [key for key, _ in published] == ["FR", "ES", "DE"]


def test_cassandra_resume_with_last_event_id_and_no_duplicate_invalids(
    tmp_path: Path,
) -> None:
    """Restarting via CassandraPositionStore resumes by (event_date, last_event_id)
    and does not re-emit historical invalid records or sleep across the restart gap.
    """
    session = FakeCassandraSession()

    # Initial dataset with 1 bad date row and 3 valid rows; first run processes 2 valid rows
    initial_file = _write_jsonl(
        tmp_path / "jobs.jsonl",
        [
            _make_job("00000000-0000-0000-0000-000000000000", "bad-date"),
            _make_job("11111111-1111-1111-1111-111111111111", "2026-10-01"),
            _make_job("22222222-2222-2222-2222-222222222222", "2026-10-02"),
        ],
    )

    producer1 = FakeKafkaProducer()
    replayer1 = EventReplayer(
        date_field="posted_date",
        seconds_per_day=1.0,
        cassandra_session=session,
        checkpoint_interval=2,
        sleep_fn=lambda _: None,
    )
    replayer1.replay(initial_file, topic="raw.jobs", producer=producer1, dataset="de_jobs")

    assert len(producer1.by_topic("raw.jobs")) == 2
    assert len(producer1.by_topic(contracts.INVALID_TOPIC)) == 1
    assert session.stored["de_jobs"]["last_event_id"] == "22222222-2222-2222-2222-222222222222"

    # Now append two more records (5 days later) and add a corrupt line at top
    updated_file = _write_jsonl(
        tmp_path / "jobs.jsonl",
        [
            "{corrupt json line",
            _make_job("00000000-0000-0000-0000-000000000000", "bad-date"),
            _make_job("11111111-1111-1111-1111-111111111111", "2026-10-01"),
            _make_job("22222222-2222-2222-2222-222222222222", "2026-10-02"),
            _make_job("33333333-3333-3333-3333-333333333333", "2026-10-07"),
            _make_job("44444444-4444-4444-4444-444444444444", "2026-10-09"),
        ],
    )

    sleeps: list[float] = []
    producer2 = FakeKafkaProducer()
    replayer2 = EventReplayer(
        date_field="posted_date",
        seconds_per_day=1.0,
        cassandra_session=session,
        checkpoint_interval=10,
        sleep_fn=sleeps.append,
    )
    replayer2.replay(updated_file, topic="raw.jobs", producer=producer2, dataset="de_jobs")

    # Only records 3 and 4 should be published; no duplicates and no re-sent invalid rows
    assert [msg["event_id"] for _, msg in producer2.by_topic("raw.jobs")] == [
        "33333333-3333-3333-3333-333333333333",
        "44444444-4444-4444-4444-444444444444",
    ]
    assert producer2.by_topic(contracts.INVALID_TOPIC) == []

    # Pacing must start from the first emitted row (2026-10-07 -> 2026-10-09 = 2.0s),
    # NOT sleep 5.0s for the historical gap since 2026-10-02.
    assert sleeps == [pytest.approx(2.0)]

    loaded = CassandraPositionStore(session).load("de_jobs")
    assert loaded is not None
    assert loaded.row_number == 4
    assert loaded.event_date == date(2026, 10, 9)
    assert loaded.last_event_id == "44444444-4444-4444-4444-444444444444"


def test_cassandra_corrupt_checkpoint_missing_event_date_raises() -> None:
    """CassandraPositionStore.load must raise ValueError if event_date is missing."""
    session = FakeCassandraSession()
    session.stored["broken_ds"] = {
        "row_number": 5,
        "event_date": None,
        "last_event_id": "11111111-1111-1111-1111-111111111111",
        "updated_at": None,
    }
    store = CassandraPositionStore(session)
    with pytest.raises(ValueError, match="missing event_date"):
        store.load("broken_ds")


def test_failed_delivery_prevents_saving_checkpoint(tmp_path: Path) -> None:
    """Position must NOT be saved if a send future fails or flush reports undelivered."""

    class FailingFuture:
        def get(self, timeout: float | None = None) -> None:
            raise RuntimeError("Kafka broker unavailable")

    class FailingFutureProducer(FakeKafkaProducer):
        def send(self, topic: str, key: str, value: dict[str, Any]) -> Any:
            super().send(topic, key, value)
            return FailingFuture()

    dataset_file = _write_jsonl(
        tmp_path / "fail.jsonl",
        [_make_job("11111111-1111-1111-1111-111111111111", "2026-10-01")],
    )

    store = InMemoryPositionStore()
    replayer = EventReplayer(
        date_field="posted_date",
        seconds_per_day=0.0,
        position_store=store,
        checkpoint_interval=1,
    )

    with pytest.raises(RuntimeError, match="Kafka broker unavailable"):
        replayer.replay(
            dataset_file,
            topic="raw.jobs",
            producer=FailingFutureProducer(),
            dataset="fail_ds",
        )

    assert store.load("fail_ds") is None


def test_bad_dates_and_invalid_schema_routed_to_errors_invalid(tmp_path: Path) -> None:
    """Missing/unparseable dates and schema-invalid messages go to errors.invalid."""
    missing_date = _make_job("11111111-1111-1111-1111-111111111111", "2026-10-05")
    del missing_date["posted_date"]

    bad_date = _make_job("22222222-2222-2222-2222-222222222222", "not-a-date")
    bad_uuid = _make_job("not-a-uuid", "2026-10-08")
    valid_ad = _make_job("33333333-3333-3333-3333-333333333333", "2026-10-10")

    dataset_file = _write_jsonl(
        tmp_path / "mixed.jsonl",
        [missing_date, bad_date, bad_uuid, valid_ad],
    )

    producer = FakeKafkaProducer()
    replay_file_to_kafka(
        dataset_file,
        date_field="posted_date",
        producer=producer,
        topic="raw.jobs",
        seconds_per_day=0.0,
    )

    valid_sent = producer.by_topic("raw.jobs")
    invalid_sent = producer.by_topic(contracts.INVALID_TOPIC)

    assert len(valid_sent) == 1
    assert valid_sent[0][1]["event_id"] == "33333333-3333-3333-3333-333333333333"
    assert len(invalid_sent) == 3
    assert all(key == "raw.jobs" for key, _ in invalid_sent)


def test_pacing_math_and_env_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify pacing calculation, default=5.0, and explicit argument precedence."""
    monkeypatch.delenv("REPLAY_SECONDS_PER_DAY", raising=False)
    assert resolve_seconds_per_day(None) == 5.0

    assert compute_sleep_seconds(None, date(2026, 10, 1), 2.0) == 0.0
    assert compute_sleep_seconds(date(2026, 10, 1), date(2026, 10, 1), 2.0) == 0.0
    assert compute_sleep_seconds(date(2026, 10, 1), date(2026, 10, 4), 0.5) == 1.5

    monkeypatch.setenv("REPLAY_SECONDS_PER_DAY", "9.0")
    assert resolve_seconds_per_day(None) == 9.0
    assert resolve_seconds_per_day(0.25) == 0.25