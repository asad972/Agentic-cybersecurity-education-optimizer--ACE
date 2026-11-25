"""Kafka-integrated Orchestrator runtime for ACE."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ace import contracts
from orchestrator.models import DEFAULT_LOOKBACK_DAYS, CoverageSnapshot
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


@dataclass(frozen=True)
class _FallbackTopicPartition:
    topic: str
    partition: int


def _make_topic_partition(topic: str, partition: int) -> Any:
    try:
        from kafka import TopicPartition

        return TopicPartition(topic, partition)
    except ImportError:
        return _FallbackTopicPartition(topic=topic, partition=partition)


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


def _default_consumer_factory(bootstrap_servers: str) -> Any:
    from kafka import KafkaConsumer

    return KafkaConsumer(
        bootstrap_servers=[s.strip() for s in bootstrap_servers.split(",") if s.strip()],
        enable_auto_commit=False,
        value_deserializer=lambda raw: json.loads(raw.decode("utf-8")),
    )


def collect_coverage_from_kafka(
    bootstrap_servers: str,
    *,
    poll_timeout_ms: int = 1000,
    window_days: int = DEFAULT_LOOKBACK_DAYS,
    reference_date: date | None = None,
    max_wait_seconds: float = 30.0,
    consumer_factory: Callable[[str], Any] | None = None,
) -> CoverageSnapshot:
    """Read every partition up to its current end offset and filter to the recent window."""
    factory = consumer_factory or _default_consumer_factory
    consumer = factory(bootstrap_servers)
    records: list[tuple[str, dict[str, Any]]] = []
    try:
        partitions: list[Any] = []
        for topic in COVERAGE_TOPICS:
            part_ids = consumer.partitions_for_topic(topic)
            if part_ids:
                for pid in sorted(part_ids):
                    partitions.append(_make_topic_partition(topic, pid))

        if not partitions:
            return CoverageSnapshot()

        consumer.assign(partitions)
        consumer.seek_to_beginning(*partitions)
        end_offsets: dict[Any, int] = consumer.end_offsets(partitions)
        pending = {tp for tp in partitions if end_offsets.get(tp, 0) > 0}

        deadline = time.monotonic() + max_wait_seconds
        while pending and time.monotonic() < deadline:
            polled = consumer.poll(timeout_ms=poll_timeout_ms)
            for tp, messages in polled.items():
                for msg in messages:
                    if isinstance(msg.value, dict):
                        records.append((tp.topic, msg.value))
            for tp in list(pending):
                if consumer.position(tp) >= end_offsets[tp]:
                    pending.discard(tp)

        if pending:
            logger.warning(
                "Timed out before reaching end offsets for %d partition(s): %s",
                len(pending),
                sorted(f"{tp.topic}:{tp.partition}" for tp in pending),
            )
    finally:
        consumer.close()

    return CoverageSnapshot.from_messages(
        records,
        window_days=window_days,
        reference_date=reference_date,
    )


@dataclass
class OrchestratorAgent:
    """Evaluates pipeline coverage and publishes up to 5 tasks to ``tasks.collect``."""

    planner: OrchestratorPlanner = field(default_factory=OrchestratorPlanner)
    producer_name: str = "orchestrator"
    lookback_days: int = DEFAULT_LOOKBACK_DAYS

    def run_once(
        self,
        producer: Any,
        snapshot: CoverageSnapshot | None = None,
        *,
        bootstrap_servers: str | None = None,
        poll_timeout_ms: int = 1000,
    ) -> list[dict[str, Any]]:
        """Compute one planning cycle and publish validated tasks to Kafka."""
        if snapshot is None:
            if bootstrap_servers:
                snapshot = collect_coverage_from_kafka(
                    bootstrap_servers,
                    poll_timeout_ms=poll_timeout_ms,
                    window_days=self.lookback_days,
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
            try:
                self.run_once(producer, bootstrap_servers=bootstrap_servers)
            except Exception:
                logger.exception("Orchestrator planning cycle failed; will retry on next cycle")
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            sleep_fn(interval_seconds)
        return cycles
