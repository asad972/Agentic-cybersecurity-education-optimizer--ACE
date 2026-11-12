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

    def flush(self) -> None:
        self.flushed_at_counts.append(len(self.sent))

    def by_topic(self, topic: str) -> list[tuple[str, dict[str, Any]]]:
        return [(k, v) for t, k, v in self.sent if t == topic]


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
    lines = [
        row if isinstance(row, str) else json.dumps(row)
        for row in rows
    ]
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
    # Verify contracts.key_of is used (country code, not hardcoded EU)
    assert [key for key, _ in published] == ["FR", "ES", "DE"]
    assert producer.flushed_at_counts[-1] == 3


def test_resume_from_saved_position_no_duplicates_no_gaps(tmp_path: Path) -> None:
    """Restarting with a saved position resumes at the exact next record."""
    dataset_file = _write_jsonl(
        tmp_path / "jobs.jsonl",
        [
            _make_job("11111111-1111-1111-1111-111111111111", "2026-10-01"),
            _make_job("22222222-2222-2222-2222-222222222222", "2026-10-02"),
            _make_job("33333333-3333-3333-3333-333333333333", "2026-10-03"),
            _make_job("44444444-4444-4444-4444-444444444444", "2026-10-04"),
        ],
    )

    store = InMemoryPositionStore()

    # Simulate an initial run that stopped after publishing the first 2 records
    store.save(
        ReplayCheckpoint(
            dataset="jobs",
            row_number=2,
            event_date=date(2026, 10, 2),
            last_event_id="22222222-2222-2222-2222-222222222222",
        )
    )

    # Also test adding a corrupted JSON line before resuming: stable key must still align
    _write_jsonl(
        dataset_file,
        [
            "{not valid json",
            _make_job("11111111-1111-1111-1111-111111111111", "2026-10-01"),
            _make_job("22222222-2222-2222-2222-222222222222", "2026-10-02"),
            _make_job("33333333-3333-3333-3333-333333333333", "2026-10-03"),
            _make_job("44444444-4444-4444-4444-444444444444", "2026-10-04"),
        ],
    )

    producer = FakeKafkaProducer()
    replayer = EventReplayer(
        date_field="posted_date",
        seconds_per_day=0.0,
        position_store=store,
        checkpoint_interval=10,  # Larger than remaining records to verify end-of-stream save
    )
    replayer.replay(dataset_file, topic="raw.jobs", producer=producer, dataset="jobs")

    published_ids = [msg["event_id"] for _, msg in producer.by_topic("raw.jobs")]
    assert published_ids == [
        "33333333-3333-3333-3333-333333333333",
        "44444444-4444-4444-4444-444444444444",
    ]

    # Final checkpoint must be saved at the end even when count < checkpoint_interval
    saved = store.load("jobs")
    assert saved is not None
    assert saved.row_number == 4
    assert saved.event_date == date(2026, 10, 4)
    assert saved.last_event_id == "44444444-4444-4444-4444-444444444444"


def test_bad_dates_routed_to_errors_invalid(tmp_path: Path) -> None:
    """Records with missing or unparseable dates must go to errors.invalid, never first."""
    missing_date = _make_job("11111111-1111-1111-1111-111111111111", "2026-10-05")
    del missing_date["posted_date"]

    bad_date = _make_job("22222222-2222-2222-2222-222222222222", "not-a-date")
    valid_ad = _make_job("33333333-3333-3333-3333-333333333333", "2026-10-10")

    dataset_file = _write_jsonl(
        tmp_path / "bad_dates.jsonl",
        [missing_date, bad_date, valid_ad],
    )

    producer = FakeKafkaProducer()
    replayer = EventReplayer(date_field="posted_date", seconds_per_day=0.0)
    replayer.replay(dataset_file, topic="raw.jobs", producer=producer)

    valid_sent = producer.by_topic("raw.jobs")
    invalid_sent = producer.by_topic(contracts.INVALID_TOPIC)

    assert len(valid_sent) == 1
    assert valid_sent[0][1]["event_id"] == "33333333-3333-3333-3333-333333333333"
    assert len(invalid_sent) == 2
    assert all(key == "raw.jobs" for key, _ in invalid_sent)


def test_schema_invalid_message_routed_via_contracts(tmp_path: Path) -> None:
    """Messages with valid dates that fail JSON schema validation go to errors.invalid."""
    invalid_schema_ad = {
        "event_id": "not-a-valid-uuid",
        "source": "synthetic",
        "country": "DE",
        "posted_date": "2026-10-01",
        "job_title": "Analyst",
        "description": "Valid date but invalid UUID",
    }
    dataset_file = _write_jsonl(tmp_path / "schema_invalid.jsonl", [invalid_schema_ad])

    producer = FakeKafkaProducer()
    replay_file_to_kafka(
        dataset_file,
        date_field="posted_date",
        producer=producer,
        topic="raw.jobs",
        seconds_per_day=0.0,
    )

    assert producer.by_topic("raw.jobs") == []
    invalid_sent = producer.by_topic(contracts.INVALID_TOPIC)
    assert len(invalid_sent) == 1
    assert "event_id" in invalid_sent[0][1]["reason"]


def test_pacing_math_and_env_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify pacing sleep calculation and that explicit seconds_per_day wins over env."""
    assert compute_sleep_seconds(None, date(2026, 10, 1), 2.0) == 0.0
    assert compute_sleep_seconds(date(2026, 10, 1), date(2026, 10, 1), 2.0) == 0.0
    assert compute_sleep_seconds(date(2026, 10, 1), date(2026, 10, 4), 0.5) == 1.5

    monkeypatch.setenv("REPLAY_SECONDS_PER_DAY", "9.0")
    assert resolve_seconds_per_day(None) == 9.0
    assert resolve_seconds_per_day(0.25) == 0.25

    dataset_file = _write_jsonl(
        tmp_path / "pacing.jsonl",
        [
            _make_job("11111111-1111-1111-1111-111111111111", "2026-10-01"),
            _make_job("22222222-2222-2222-2222-222222222222", "2026-10-01"),
            _make_job("33333333-3333-3333-3333-333333333333", "2026-10-04"),
        ],
    )

    sleeps: list[float] = []
    producer = FakeKafkaProducer()
    replayer = EventReplayer(
        date_field="posted_date",
        seconds_per_day=0.2,
        sleep_fn=sleeps.append,
    )
    replayer.replay(dataset_file, topic="raw.jobs", producer=producer)

    # Same-day event sleeps 0s (not called); 3-day jump sleeps 3 * 0.2 = 0.6s
    assert sleeps == [pytest.approx(0.6)]


def test_cassandra_position_store_persists_after_flush(tmp_path: Path) -> None:
    """CassandraPositionStore must only write after producer.flush() is called."""
    events: list[str] = []

    class RecordingProducer(FakeKafkaProducer):
        def send(self, topic: str, key: str, value: dict[str, Any]) -> None:
            events.append("send")
            super().send(topic, key, value)

        def flush(self) -> None:
            events.append("flush")
            super().flush()

    class FakeCassandraSession:
        def __init__(self) -> None:
            self.stored: dict[str, dict[str, Any]] = {}

        def execute(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
            if query.lstrip().upper().startswith("SELECT"):
                row = self.stored.get(params[0])
                return [row] if row else []
            events.append("cassandra_save")
            dataset, row_number, event_date, updated_at = params
            self.stored[dataset] = {
                "row_number": row_number,
                "event_date": event_date,
                "updated_at": updated_at,
            }
            return []

    dataset_file = _write_jsonl(
        tmp_path / "cassandra_test.jsonl",
        [
            _make_job("11111111-1111-1111-1111-111111111111", "2026-10-01"),
            _make_job("22222222-2222-2222-2222-222222222222", "2026-10-02"),
        ],
    )

    session = FakeCassandraSession()
    producer = RecordingProducer()
    replayer = EventReplayer(
        date_field="posted_date",
        seconds_per_day=0.0,
        cassandra_session=session,
        checkpoint_interval=1,
    )
    replayer.replay(dataset_file, topic="raw.jobs", producer=producer, dataset="de_jobs")

    # Every cassandra_save must be preceded immediately by flush
    assert events == [
        "send",
        "flush",
        "cassandra_save",
        "send",
        "flush",
        "cassandra_save",
        "flush",
    ]
    loaded = CassandraPositionStore(session).load("de_jobs")
    assert loaded is not None
    assert loaded.row_number == 2
    assert loaded.event_date == date(2026, 10, 2)