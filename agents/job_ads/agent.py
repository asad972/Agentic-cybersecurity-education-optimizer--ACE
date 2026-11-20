"""Job ads dataset replay agent backed by ace.replay and ace.contracts."""

from __future__ import annotations

import csv
import json
import logging
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ace import contracts
from ace.replay import (
    CassandraPositionStore,
    EventReplayer,
    ReplayCheckpoint,
    ReplayPositionStore,
)
from job_ads.normalizer import normalize_job_row

logger = logging.getLogger(__name__)

RAW_JOBS_TOPIC = contracts.topic("raw.jobs").name


class KafkaJsonProducer:
    """Thin wrapper around kafka.KafkaProducer matching ace.replay's producer protocol."""

    def __init__(self, bootstrap_servers: str, producer: Any = None) -> None:
        if producer is None:
            from kafka import KafkaProducer

            producer = KafkaProducer(
                bootstrap_servers=[s.strip() for s in bootstrap_servers.split(",") if s.strip()],
                key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
                value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
                acks="all",
            )
        self._producer = producer

    def send(self, topic: str, key: str | None = None, value: Any = None) -> Any:
        return self._producer.send(topic, key=key, value=value)

    def flush(self) -> Any:
        return self._producer.flush()

    def close(self) -> None:
        try:
            self.flush()
        finally:
            if hasattr(self._producer, "close"):
                self._producer.close()


class DryRunProducer:
    """In-memory producer for local verification without a running Kafka broker."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str | None, dict[str, Any]]] = []

    def send(self, topic: str, key: str | None = None, value: Any = None) -> None:
        self.sent.append((topic, key, value))

    def flush(self) -> None:
        return None


def open_cassandra_store(
    hosts: str,
    keyspace: str = "ace",
) -> tuple[Any, CassandraPositionStore]:
    """Connect to Cassandra and return (cluster, CassandraPositionStore)."""
    from cassandra.cluster import Cluster

    contact_points = [h.strip() for h in hosts.split(",") if h.strip()]
    cluster = Cluster(contact_points)
    session = cluster.connect()
    return cluster, CassandraPositionStore(session, keyspace=keyspace)


def _csv_to_temp_jsonl(csv_path: Path, tmp_dir: Path) -> Path:
    """Convert a CSV dataset file into a temporary JSONL file for EventReplayer."""
    out_path = tmp_dir / f"{csv_path.stem}.jsonl"
    with (
        csv_path.open("r", encoding="utf-8-sig", newline="") as src,
        out_path.open("w", encoding="utf-8", newline="\n") as dst,
    ):
        reader = csv.DictReader(src)
        for row in reader:
            dst.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    return out_path


def _parquet_to_temp_jsonl(parquet_path: Path, tmp_dir: Path) -> Path:
    """Convert a Parquet dataset file (e.g. Hugging Face dataset) into a temporary JSONL file."""
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "Reading .parquet files requires the 'pyarrow' package to be installed"
        ) from exc

    out_path = tmp_dir / f"{parquet_path.stem}.jsonl"
    parquet_file = pq.ParquetFile(parquet_path)
    with out_path.open("w", encoding="utf-8", newline="\n") as dst:
        for batch in parquet_file.iter_batches(batch_size=4096):
            for row in batch.to_pylist():
                dst.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return out_path


@dataclass
class JobAdsAgent:
    """Replays job advertisement datasets onto Kafka topic ``raw.jobs``."""

    default_source: str = "job_ads_dataset"
    default_country: str | None = None
    enforce_europe_scope: bool = True
    seconds_per_day: float | None = None
    checkpoint_interval: int = 25
    position_store: ReplayPositionStore | None = None
    producer_name: str = "job-ads-agent"
    last_skipped_count: int = field(default=0, init=False)

    def map_row(self, raw_row: dict[str, Any]) -> dict[str, Any] | None:
        mapped = normalize_job_row(
            raw_row,
            default_source=self.default_source,
            default_country=self.default_country,
            enforce_europe_scope=self.enforce_europe_scope,
        )
        if mapped is None:
            self.last_skipped_count += 1
        return mapped

    def replay_paths(
        self,
        paths: str | Path | Sequence[str | Path],
        producer: Any,
        *,
        dataset: str = "job_ads",
        on_progress: Callable[[ReplayCheckpoint], None] | None = None,
    ) -> int:
        """Replay one or more JSONL, CSV, or Parquet dataset files in event-time order."""
        self.last_skipped_count = 0

        if isinstance(paths, (str, Path)):
            input_paths = [Path(paths)]
        else:
            input_paths = [Path(p) for p in paths]

        replayer = EventReplayer(
            date_field="posted_date",
            seconds_per_day=self.seconds_per_day,
            producer_name=self.producer_name,
            position_store=self.position_store,
            checkpoint_interval=self.checkpoint_interval,
        )

        with tempfile.TemporaryDirectory(prefix="ace_job_ads_") as tmp_name:
            tmp_dir = Path(tmp_name)
            jsonl_paths: list[Path] = []
            for path in input_paths:
                suffix = path.suffix.lower()
                if suffix == ".csv":
                    jsonl_paths.append(_csv_to_temp_jsonl(path, tmp_dir))
                elif suffix == ".parquet":
                    jsonl_paths.append(_parquet_to_temp_jsonl(path, tmp_dir))
                else:
                    jsonl_paths.append(path)

            last_pos = replayer.replay(
                file_paths=jsonl_paths,
                topic=RAW_JOBS_TOPIC,
                producer=producer,
                dataset=dataset,
                row_to_message=self.map_row,
                on_progress=on_progress,
            )

        if self.last_skipped_count > 0:
            logger.warning(
                "Skipped %d row(s) outside European Skills Scope during replay of dataset=%s",
                self.last_skipped_count,
                dataset,
            )
        else:
            logger.info(
                "Skipped 0 rows outside European Skills Scope during replay of dataset=%s",
                dataset,
            )
        return last_pos