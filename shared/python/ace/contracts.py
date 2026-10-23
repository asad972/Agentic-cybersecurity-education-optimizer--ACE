"""Topic names and message checks shared by every agent and stage.

Everything comes from shared/schemas/topics.csv (topic, schema, key) and the
JSON schemas next to it, so a topic name is never hand-typed: an unknown name
fails at once. Every message is checked against its schema before it is
published; a message that fails goes to errors.invalid with the reason
instead of being dropped.

    from ace import contracts

    out = contracts.route("skills.demand", message, producer="s2-extract")
    producer.send(out.topic, key=out.key, value=out.value)
"""

from __future__ import annotations

import csv
import difflib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

INVALID_TOPIC = "errors.invalid"


class UnknownTopic(KeyError):
    """A topic name that is not in topics.csv."""


class InvalidMessage(ValueError):
    """A message that does not match its topic's schema."""

    def __init__(self, topic: str, reason: str):
        super().__init__(f"{topic}: {reason}")
        self.topic = topic
        self.reason = reason


@dataclass(frozen=True)
class Topic:
    name: str
    schema: str
    key_field: str


@dataclass(frozen=True)
class Outgoing:
    """A message ready to publish: where it goes, its Kafka key and its value."""

    topic: str
    key: str
    value: dict[str, Any]


def schemas_dir() -> Path:
    """shared/schemas in the repo, or ACE_SCHEMAS_DIR inside a container."""
    configured = os.environ.get("ACE_SCHEMAS_DIR")
    path = Path(configured) if configured else Path(__file__).resolve().parents[2] / "schemas"
    if not (path / "topics.csv").is_file():
        raise FileNotFoundError(
            f"topics.csv not found in {path}; set ACE_SCHEMAS_DIR to the folder with the schemas"
        )
    return path


@cache
def topics() -> dict[str, Topic]:
    with open(schemas_dir() / "topics.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {r["topic"]: Topic(r["topic"], r["schema"], r["key"]) for r in rows}


def topic(name: str) -> Topic:
    """The topic called name; raises UnknownTopic, with a suggestion, for a typo."""
    known = topics()
    if name not in known:
        close = difflib.get_close_matches(name, known, n=1)
        hint = f"; did you mean {close[0]!r}?" if close else ""
        raise UnknownTopic(f"unknown topic {name!r}{hint}")
    return known[name]


@cache
def _validator(schema: str) -> Draft202012Validator:
    path = schemas_dir() / f"{schema}.schema.json"
    definition = json.loads(path.read_text(encoding="utf-8"))
    return Draft202012Validator(definition, format_checker=Draft202012Validator.FORMAT_CHECKER)


def check(topic_name: str, message: Any) -> str | None:
    """Why message does not fit topic_name, or None when it does."""
    errors = sorted(
        _validator(topic(topic_name).schema).iter_errors(message), key=lambda e: list(e.path)
    )
    if not errors:
        return None
    first = errors[0]
    where = "/".join(str(p) for p in first.path)
    return f"{where}: {first.message}" if where else first.message


def validate(topic_name: str, message: Any) -> None:
    """Raise InvalidMessage when message does not fit topic_name."""
    reason = check(topic_name, message)
    if reason is not None:
        raise InvalidMessage(topic_name, reason)


def key_of(topic_name: str, message: dict[str, Any]) -> str:
    """The Kafka key of a valid message, e.g. its country."""
    return str(message[topic(topic_name).key_field])


def invalid_record(
    topic_name: str,
    producer: str,
    reason: str,
    payload: Any,
    detected_at: datetime | None = None,
) -> dict[str, Any]:
    """The errors.invalid message for a message that failed its check."""
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False, default=str)
    when = detected_at or datetime.now(timezone.utc)
    record = {
        "topic": topic_name,
        "producer": producer,
        "reason": reason,
        "payload": payload,
        "detected_at": when.isoformat().replace("+00:00", "Z"),
    }
    validate(INVALID_TOPIC, record)
    return record


def route(topic_name: str, message: Any, producer: str) -> Outgoing:
    """Where message should be published: its own topic, or errors.invalid if it fails."""
    reason = check(topic_name, message)
    if reason is None:
        return Outgoing(topic_name, key_of(topic_name, message), message)
    return Outgoing(INVALID_TOPIC, topic_name, invalid_record(topic_name, producer, reason, message))
