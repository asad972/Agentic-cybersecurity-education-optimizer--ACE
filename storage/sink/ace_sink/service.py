"""The sink service: reads Kafka, writes each message's rows, then marks it as done.

A message's Kafka offset is committed only after its rows are written, or
after it has been delivered to errors.invalid. If the sink stops in between,
it reads the message again after a restart and writes the same rows again,
which changes nothing (upserts).

- A message that is not JSON, fails its schema check, or is Unstorable goes to
  errors.invalid, and the sink moves on.
- Any other error stops the sink without committing, so no message is lost:
  Cassandra being unavailable, errors.invalid not accepting the record, or a
  bug in a row function. Docker or Kubernetes restarts it.
"""

from __future__ import annotations

import json
import logging
import os
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from ace import contracts
from ace_sink import rows as sink_rows
from ace_sink.rows import Unstorable

PRODUCER = "sink"

log = logging.getLogger(__name__)


def _names(text: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (text or "").split(",") if part.strip())


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Settings:
    kafka_bootstrap: str
    group_id: str
    topics: tuple[str, ...]
    cassandra_hosts: tuple[str, ...]
    cassandra_port: int
    cassandra_keyspace: str
    cassandra_local_dc: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        """Settings from environment variables; SINK_TOPICS switches topics on (default: all)."""
        handled = sink_rows.topics()
        chosen = _names(env.get("SINK_TOPICS")) or tuple(handled)
        unknown = [name for name in chosen if name not in handled]
        if unknown:
            raise ValueError(f"SINK_TOPICS: the sink has no row function for {unknown}")
        return cls(
            kafka_bootstrap=env.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
            group_id=env.get("SINK_GROUP_ID", "ace-sink"),
            topics=chosen,
            cassandra_hosts=_names(env.get("CASSANDRA_HOSTS", "localhost")),
            cassandra_port=int(env.get("CASSANDRA_PORT", "9042")),
            cassandra_keyspace=env.get("CASSANDRA_KEYSPACE", "ace"),
            cassandra_local_dc=env.get("CASSANDRA_LOCAL_DC", "datacenter1"),
        )


def handle(topic: str, value: bytes, received_at: datetime, writer: Any) -> dict[str, Any] | None:
    """Write one Kafka message. Returns its errors.invalid record if it cannot be stored."""
    try:
        message = json.loads(value)
    except (TypeError, ValueError) as error:
        payload = value.decode("utf-8", "replace") if isinstance(value, bytes) else repr(value)
        return contracts.invalid_record(topic, PRODUCER, f"not JSON: {error}", payload)
    reason = contracts.check(topic, message)
    if reason is None:
        try:
            writer.write(sink_rows.rows(topic, message, received_at))
            return None
        except Unstorable as error:
            reason = f"cannot be stored: {error}"
    return contracts.invalid_record(topic, PRODUCER, reason, message)


def publish_invalid(producer: Any, invalid: dict[str, Any]) -> None:
    """Deliver one errors.invalid record; raises if Kafka did not confirm it."""
    failed = []
    producer.produce(
        contracts.INVALID_TOPIC,
        key=contracts.key_of(contracts.INVALID_TOPIC, invalid),
        value=json.dumps(invalid, ensure_ascii=False).encode("utf-8"),
        on_delivery=lambda error, _record: failed.append(error) if error else None,
    )
    if producer.flush(10) or failed:
        raise RuntimeError(f"errors.invalid did not accept the record: {failed or 'timed out'}")


def run(settings: Settings, consumer: Any, producer: Any, writer: Any,
        stop: Callable[[], bool] = lambda: False, clock: Callable[[], datetime] = _now) -> None:
    """Consume until stop() is true. consumer and producer are confluent_kafka clients.

    Offsets are committed asynchronously after each message, and once more,
    synchronously, when the sink stops normally.
    """
    consumer.subscribe(list(settings.topics))
    log.info("sink started for %d topics: %s", len(settings.topics), ", ".join(settings.topics))
    last = None
    while not stop():
        record = consumer.poll(1.0)
        if record is None:
            continue
        if record.error():
            log.warning("Kafka error: %s", record.error())
            continue
        invalid = handle(record.topic(), record.value(), clock(), writer)
        if invalid is not None:
            log.warning("invalid message on %s: %s", record.topic(), invalid["reason"])
            publish_invalid(producer, invalid)
        consumer.commit(message=record, asynchronous=True)
        last = record
    if last is not None:
        consumer.commit(message=last, asynchronous=False)


def main() -> None:
    """Entry point of the container: connect to Kafka and Cassandra, then run until stopped."""
    from cassandra import InvalidRequest
    from cassandra.cluster import Cluster
    from cassandra.policies import DCAwareRoundRobinPolicy
    from confluent_kafka import Consumer, Producer

    from ace_sink.writer import CassandraWriter

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    cluster = Cluster(
        list(settings.cassandra_hosts),
        port=settings.cassandra_port,
        protocol_version=4,
        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc=settings.cassandra_local_dc),
    )
    session = cluster.connect(settings.cassandra_keyspace)
    consumer = Consumer({
        "bootstrap.servers": settings.kafka_bootstrap,
        "group.id": settings.group_id,
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    })
    producer = Producer({"bootstrap.servers": settings.kafka_bootstrap})

    stopping = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopping.append(True))
    try:
        writer = CassandraWriter(session, rejected=(InvalidRequest,))
        run(settings, consumer, producer, writer, stop=lambda: bool(stopping))
    finally:
        consumer.close()
        producer.flush(10)
        cluster.shutdown()
        log.info("sink stopped")
