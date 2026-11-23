"""The exporter service: reads Kafka, checks the LLM spend, serves /metrics.

- Reads every topic in counts.TOPICS with its own consumer group
  (EXPORTER_GROUP_ID, default ace-monitoring). It only reads: it never
  publishes a message and never writes to Cassandra. Its offsets are committed
  automatically, so after a restart it carries on where it stopped; Prometheus
  handles the counters starting again from 0. On its very first start it reads
  everything Kafka still keeps.
- Every LLM_SPEND_INTERVAL_SECONDS it reads today's spend from llm_cache.
- Serves the metrics on EXPORTER_PORT (default 9400) at /metrics.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from ace_exporter import llm_spend
from ace_exporter.counts import TOPICS, PipelineCounts

log = logging.getLogger(__name__)


def _names(text: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (text or "").split(",") if part.strip())


def _budget(text: str | None) -> float | None:
    """LLM_DAILY_BUDGET_EUR as a number; a trailing comment, as in .env.example, is ignored."""
    value = (text or "").split("#", 1)[0].strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        log.warning("LLM_DAILY_BUDGET_EUR is not a number: %r", text)
        return None


@dataclass(frozen=True)
class Settings:
    kafka_bootstrap: str
    group_id: str
    port: int
    cassandra_hosts: tuple[str, ...]
    cassandra_port: int
    cassandra_keyspace: str
    cassandra_local_dc: str
    spend_interval_seconds: float
    daily_budget_eur: float | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        interval = float(env.get("LLM_SPEND_INTERVAL_SECONDS", "300"))
        if interval <= 0:
            raise ValueError("LLM_SPEND_INTERVAL_SECONDS must be above 0")
        return cls(
            kafka_bootstrap=env.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            group_id=env.get("EXPORTER_GROUP_ID", "ace-monitoring"),
            port=int(env.get("EXPORTER_PORT", "9400")),
            cassandra_hosts=_names(env.get("CASSANDRA_HOSTS", "localhost")),
            cassandra_port=int(env.get("CASSANDRA_PORT", "9042")),
            cassandra_keyspace=env.get("CASSANDRA_KEYSPACE", "ace"),
            cassandra_local_dc=env.get("CASSANDRA_LOCAL_DC", "datacenter1"),
            spend_interval_seconds=interval,
            daily_budget_eur=_budget(env.get("LLM_DAILY_BUDGET_EUR")),
        )

    def consumer_config(self) -> dict[str, Any]:
        return {
            "bootstrap.servers": self.kafka_bootstrap,
            "group.id": self.group_id,
            "client.id": "ace-pipeline-exporter",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
        }


def consume(consumer: Any, counts: PipelineCounts, stop: Callable[[], bool]) -> None:
    """Count every message until stop() is true. consumer is a confluent_kafka Consumer."""
    consumer.subscribe(list(TOPICS))
    log.info("reading %d topics: %s", len(TOPICS), ", ".join(TOPICS))
    while not stop():
        record = consumer.poll(1.0)
        if record is None:
            continue
        if record.error():
            log.warning("Kafka error: %s", record.error())
            continue
        counts.add(record.topic(), record.value())


class SpendChecker:
    """Holds the Cassandra connection and the latest spend result for the collector."""

    def __init__(self, settings: Settings, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.settings = settings
        self.clock = clock
        self.state = llm_spend.SpendState()
        self._cluster = None
        self._session = None

    def _connect(self) -> Any:
        if self._session is None:
            from cassandra.cluster import Cluster
            from cassandra.policies import DCAwareRoundRobinPolicy

            s = self.settings
            cluster = Cluster(list(s.cassandra_hosts), port=s.cassandra_port, protocol_version=4,
                              load_balancing_policy=DCAwareRoundRobinPolicy(local_dc=s.cassandra_local_dc))
            try:
                self._session = cluster.connect(s.cassandra_keyspace)
            except Exception:
                cluster.shutdown()
                raise
            self._cluster = cluster
        return self._session

    def check(self) -> None:
        self.state = llm_spend.check(self._connect, self.clock())
        if not self.state.cassandra_up:
            self.close()  # connect afresh next time

    def close(self) -> None:
        if self._cluster is not None:
            self._cluster.shutdown()
        self._cluster = self._session = None

    def run(self, stopping: threading.Event) -> None:
        while not stopping.is_set():
            self.check()
            stopping.wait(self.settings.spend_interval_seconds)
        self.close()


def main() -> None:
    """Entry point of the container."""
    from confluent_kafka import Consumer
    from prometheus_client import REGISTRY, start_http_server

    from ace_exporter.collector import PipelineCollector

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    counts = PipelineCounts()
    spend = SpendChecker(settings)
    REGISTRY.register(PipelineCollector(counts, lambda: spend.state, settings.daily_budget_eur))
    start_http_server(settings.port)
    log.info("metrics on port %d", settings.port)

    stopping = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.set())
    spend_thread = threading.Thread(target=spend.run, args=(stopping,), name="llm-spend", daemon=True)
    spend_thread.start()

    consumer = Consumer(settings.consumer_config())
    try:
        consume(consumer, counts, stop=stopping.is_set)
    finally:
        consumer.close()
        spend_thread.join(timeout=10)
        log.info("exporter stopped")
