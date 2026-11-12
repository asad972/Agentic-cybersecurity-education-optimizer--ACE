"""Shared input and Kafka publishing helpers for the M5 agents."""

import csv
import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from ace import contracts


class Producer(Protocol):
    """The small Kafka producer interface used by the M5 publishers."""

    def send(self, topic: str, *, key: bytes, value: bytes) -> Any: ...


class KafkaUnavailable(RuntimeError):
    """Kafka could not be reached using the configured bootstrap servers."""


def _send(outgoing: contracts.Outgoing, producer: Producer) -> Any:
    return producer.send(
        outgoing.topic,
        key=outgoing.key.encode("utf-8"),
        value=json.dumps(outgoing.value, ensure_ascii=False).encode("utf-8"),
    )


def read_records(path: str | Path) -> list[Mapping[str, Any]]:
    """Read canonical records from a JSON array, JSONL, or CSV file."""
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        with source.open(newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames is None:
                raise ValueError(f"{source} has no CSV header")
            return [
                {key: value if value != "" else None for key, value in row.items()}
                for row in reader
                if any(value not in (None, "") for value in row.values())
            ]

    if suffix == ".json":
        payload = json.loads(source.read_text(encoding="utf-8"))
        records = payload if isinstance(payload, list) else [payload]
    elif suffix == ".jsonl":
        records = []
        with source.open(encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"{source}:{line_number} is not valid JSON"
                    ) from error
    else:
        raise ValueError(f"{source} must have a .json, .jsonl, or .csv extension")

    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ValueError(f"{source} must contain a JSON object or array of objects")
    if any(not isinstance(record, Mapping) for record in records):
        raise ValueError(f"{source} must contain only JSON objects")
    return records


def publish_records(
    topic: str,
    producer_name: str,
    records: Sequence[Mapping[str, Any]],
    producer: Producer,
) -> int:
    """Validate and send records via the shared ACE topic contracts."""
    deliveries = []
    for record in records:
        outgoing = contracts.route(topic, dict(record), producer=producer_name)
        deliveries.append(_send(outgoing, producer))
    for delivery in deliveries:
        if delivery is not None:
            delivery.get()
    return len(records)


def publish_normalized_records(
    topic: str,
    producer_name: str,
    records: Sequence[Mapping[str, Any]],
    normalize: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    producer: Producer,
) -> int:
    """Publish valid rows and route row-level normalization failures as invalid."""
    deliveries = []
    for row_number, record in enumerate(records, start=1):
        try:
            message = dict(normalize(record))
            outgoing = contracts.route(topic, message, producer=producer_name)
        except ValueError as error:
            invalid = contracts.invalid_record(
                topic,
                producer_name,
                f"row {row_number}: {error}",
                dict(record),
            )
            outgoing = contracts.Outgoing(
                contracts.INVALID_TOPIC,
                topic,
                invalid,
            )
        deliveries.append(_send(outgoing, producer))

    for delivery in deliveries:
        if delivery is not None:
            delivery.get()
    return len(records)


def create_producer() -> Any:
    """Create the Kafka producer configured for ACE's internal broker."""
    from kafka import KafkaProducer
    from kafka.errors import NoBrokersAvailable

    bootstrap_servers = os.environ.get(
        "KAFKA_BOOTSTRAP_SERVERS", "kafka:29092"
    )
    try:
        return KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            acks="all",
            retries=5,
        )
    except NoBrokersAvailable as error:
        raise KafkaUnavailable(
            "Kafka is unavailable at KAFKA_BOOTSTRAP_SERVERS="
            f"{bootstrap_servers}"
        ) from error


def publish_file(
    topic: str,
    producer_name: str,
    records: Sequence[Mapping[str, Any]],
) -> int:
    """Publish to Kafka and flush before the process exits."""
    producer = create_producer()
    try:
        count = publish_records(topic, producer_name, records, producer)
        producer.flush()
        return count
    finally:
        producer.close()


def publish_normalized_file(
    topic: str,
    producer_name: str,
    records: Sequence[Mapping[str, Any]],
    normalize: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> int:
    """Create a Kafka producer and publish normalized rows before exiting."""
    producer = create_producer()
    try:
        count = publish_normalized_records(
            topic, producer_name, records, normalize, producer
        )
        producer.flush()
        return count
    finally:
        producer.close()


def parse_json_list(value: Any, field: str) -> Any:
    """Decode JSON-encoded list values from CSV cells."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"{field} in CSV must be a JSON value") from error


def parse_integer(value: Any, field: str) -> Any:
    """Convert a CSV integer cell while preserving non-string values."""
    if value is None or not isinstance(value, str):
        return value
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{field} in CSV must be an integer") from error
