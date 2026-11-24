"""What the exporter counts in each Kafka message.

Plain Python with no Kafka or Prometheus, so every rule is tested with plain
messages. The service feeds every message it reads into PipelineCounts.add();
the collector turns a snapshot into Prometheus metrics.

| Topic | What is counted |
| --- | --- |
| raw.jobs, raw.profiles, raw.programmes, raw.workforce, threats.events | records per topic and source (ingestion) |
| jobs.clean | records per source (what S1 kept) |
| skills.demand, skills.supply | records per source, and Skill, Certification and Unmapped mentions |
| errors.invalid | invalid messages per target topic and producer |
| tasks.collect | orchestrator tasks per agent, action and decided_by |

Derived values (duplicates removed by S1, extraction success rate, Unmapped
share) are worked out from these counters by Prometheus recording rules, so
they stay right across exporter restarts.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ace import contracts

UNKNOWN = "unknown"
OTHER = "other"


def _topics(*names: str) -> tuple[str, ...]:
    """Topic names checked against topics.csv, so a typo fails at import."""
    return tuple(contracts.topic(name).name for name in names)


INGESTED = _topics("raw.jobs", "raw.profiles", "raw.programmes", "raw.workforce", "threats.events")
CLEAN = _topics("jobs.clean")
EXTRACTED = _topics("skills.demand", "skills.supply")
RECORD_TOPICS = INGESTED + CLEAN + EXTRACTED
(INVALID,) = _topics(contracts.INVALID_TOPIC)
(TASKS,) = _topics("tasks.collect")

# Every topic the exporter reads.
TOPICS = RECORD_TOPICS + (INVALID, TASKS)

# Mention lists in skills.demand / skills.supply and the kind label they are counted under.
MENTION_FIELDS = (("skills", "skill"), ("certifications", "certification"), ("unmapped", "unmapped"))


class Labels:
    """Keeps label values short and few, whatever the messages contain.

    Sources and producers are free text written by each agent. One label value
    is one Prometheus time series, so values are cut to max_length characters,
    and after max_values different values for one label every new one is
    counted as "other".
    """

    def __init__(self, max_values: int = 50, max_length: int = 64):
        self.max_values = max_values
        self.max_length = max_length
        self._seen: dict[str, set[str]] = {}

    def value(self, label: str, raw: Any) -> str:
        if not isinstance(raw, str) or not raw.strip():
            return UNKNOWN
        text = raw.strip()[: self.max_length]
        seen = self._seen.setdefault(label, set())
        if text in seen:
            return text
        if len(seen) >= self.max_values:
            return OTHER
        seen.add(text)
        return text


@dataclass(frozen=True)
class Snapshot:
    """The counts at one moment; every value only grows while the exporter runs."""

    records: dict[tuple[str, str], int] = field(default_factory=dict)            # (topic, source)
    mentions: dict[tuple[str, str], int] = field(default_factory=dict)           # (topic, kind)
    invalid: dict[tuple[str, str], int] = field(default_factory=dict)            # (topic, producer)
    tasks: dict[tuple[str, str, str], int] = field(default_factory=dict)         # (agent, action, decided_by)
    unreadable: dict[str, int] = field(default_factory=dict)                     # topic


def parse(value: bytes | str | None) -> dict[str, Any] | None:
    """The message as a dict, or None if it is not a JSON object."""
    if value is None:
        return None
    try:
        message = json.loads(value)
    except (TypeError, ValueError):
        return None
    return message if isinstance(message, dict) else None


def mentions(message: dict[str, Any]) -> dict[str, int]:
    """How many Skills, Certifications and Unmapped mentions one skills.* message has."""
    counts = {}
    for field_name, kind in MENTION_FIELDS:
        items = message.get(field_name)
        counts[kind] = sum(1 for item in items if isinstance(item, str)) if isinstance(items, list) else 0
    return counts


class PipelineCounts:
    """Running counts of everything the exporter has read. Safe to read while it is being updated."""

    def __init__(self, labels: Labels | None = None):
        self._labels = labels or Labels()
        self._lock = threading.Lock()
        self._records: Counter = Counter()
        self._mentions: Counter = Counter()
        self._invalid: Counter = Counter()
        self._tasks: Counter = Counter()
        self._unreadable: Counter = Counter()

    def add(self, topic: str, value: bytes | str | None) -> None:
        """Count one Kafka message read from topic."""
        if topic not in TOPICS:
            return
        message = parse(value)
        with self._lock:
            if message is None:
                self._unreadable[topic] += 1
            elif topic in RECORD_TOPICS:
                self._add_record(topic, message)
            elif topic == INVALID:
                self._add_invalid(message)
            else:
                self._add_task(message)

    def _add_record(self, topic: str, message: dict[str, Any]) -> None:
        self._records[(topic, self._labels.value("source", message.get("source")))] += 1
        if topic in EXTRACTED:
            for kind, count in mentions(message).items():
                self._mentions[(topic, kind)] += count

    def _add_invalid(self, message: dict[str, Any]) -> None:
        target = message.get("topic")
        target = target if target in contracts.topics() else OTHER
        self._invalid[(target, self._labels.value("producer", message.get("producer")))] += 1

    def _add_task(self, message: dict[str, Any]) -> None:
        key = tuple(self._labels.value(name, message.get(name)) for name in ("agent", "action", "decided_by"))
        self._tasks[key] += 1

    def snapshot(self) -> Snapshot:
        with self._lock:
            return Snapshot(
                records=dict(self._records),
                mentions=dict(self._mentions),
                invalid=dict(self._invalid),
                tasks=dict(self._tasks),
                unreadable=dict(self._unreadable),
            )
