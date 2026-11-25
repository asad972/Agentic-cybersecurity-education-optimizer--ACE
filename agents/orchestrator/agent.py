"""Kafka-integrated Orchestrator runtime for ACE."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ace import contracts
from orchestrator.models import CoverageSnapshot
from orchestrator.planner import OrchestratorPlanner

logger = logging.getLogger(__name__)

TASKS_COLLECT_TOPIC = contracts.topic("tasks.collect").name

COVERAGE_TOPICS: tuple[str, ...] = (
    "agg.demand_daily",
    "raw.programmes",
    "agg.threats_daily",
    "errors.invalid",
    "analysis.platforms",
)


class KafkaJsonProducer:
    """Thin wrapper around kafka.KafkaProducer."""

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


def collect_coverage_from_kafka(
    bootstrap_servers: str,
    *,
    poll_timeout_ms: int = 2000,
    max_records: int = 5000,
) -> CoverageSnapshot:
    """Read available coverage messages from Kafka without blocking indefinitely."""
    from kafka import KafkaConsumer

    consumer = KafkaConsumer(
        *COVERAGE_TOPICS,
        bootstrap_servers=[s.strip() for s in bootstrap_servers.split(",") if s.strip()],
        group_id=f"ace-orchestrator-probe-{uuid.uuid4()}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        consumer_timeout_ms=poll_timeout_ms,
        value_deserializer=lambda raw: json.loads(raw.decode("utf-8")),
    )
    records: list[tuple[str, dict[str, Any]]] = []
    try:
        for msg in consumer:
            if isinstance(msg.value, dict):
                records.append((msg.topic, msg.value))
            if len(records) >= max_records:
                break
    finally:
        consumer.close()
    return CoverageSnapshot.from_messages(records)


@dataclass
class OrchestratorAgent:
    """Evaluates pipeline coverage and publishes up to 5 tasks to ``tasks.collect``."""

    planner: OrchestratorPlanner = field(default_factory=OrchestratorPlanner)
    producer_name: str = "orchestrator"

    def run_once(
        self,
        producer: Any,
        snapshot: CoverageSnapshot | None = None,
        *,
        bootstrap_servers: str | None = None,
        poll_timeout_ms: int = 2000,
    ) -> list[dict[str, Any]]:
        """Compute one planning cycle and publish validated tasks to Kafka."""
        if snapshot is None:
            if bootstrap_servers:
                snapshot = collect_coverage_from_kafka(
                    bootstrap_servers,
                    poll_timeout_ms=poll_timeout_ms,
                )
            else:
                snapshot = CoverageSnapshot()

        tasks = self.planner.plan(snapshot)
        published: list[dict[str, Any]] = []
        for task in tasks:
            outgoing = contracts.route(
                TASKS_COLLECT_TOPIC,
                task,
                producer=self.producer_name,
            )
            producer.send(outgoing.topic, key=outgoing.key, value=outgoing.value)
            if outgoing.topic == TASKS_COLLECT_TOPIC:
                published.append(outgoing.value)
                logger.info(
                    "Published collect_task: task_id=%s agent=%s action=%s decided_by=%s reason=%s",
                    task["task_id"],
                    task["agent"],
                    task["action"],
                    task["decided_by"],
                    task["reason"],
                )
        if hasattr(producer, "flush"):
            producer.flush()
        return published

    def run_loop(
        self,
        producer: Any,
        *,
        bootstrap_servers: str,
        interval_seconds: float = 21600.0,
        max_cycles: int | None = None,
        sleep_fn: Any = time.sleep,
    ) -> int:
        """Run periodic orchestration cycles (default: every 6 hours / 21600s)."""
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            self.run_once(producer, bootstrap_servers=bootstrap_servers)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            sleep_fn(interval_seconds)
        return cycles
