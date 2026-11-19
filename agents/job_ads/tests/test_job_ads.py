"""Tests for the Job Ads replay agent (Issue #9)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ace import contracts
from ace.replay import InMemoryPositionStore, ReplayCheckpoint
from job_ads.__main__ import main
from job_ads.agent import RAW_JOBS_TOPIC, DryRunProducer, JobAdsAgent
from job_ads.normalizer import normalize_country, normalize_job_row


def _repo_sample_path() -> Path:
    return Path(__file__).resolve().parents[3] / "shared" / "testdata" / "jobs_sample.jsonl"


def test_normalize_country_codes_and_europe_scope() -> None:
    assert normalize_country("de") == "DE"
    assert normalize_country("UK") == "GB"
    assert normalize_country("EL") == "GR"
    assert normalize_country("Germany") == "DE"
    # Non-European countries are excluded from the European Skills Scope
    assert normalize_country("US") is None
    assert normalize_country("JP") is None


def test_normalize_external_dataset_row_aliases_and_deterministic_id() -> None:
    raw = {
        "job_id": "adzuna-998877",
        "portal": "adzuna",
        "location": {"country": "UK"},
        "created": "2026-10-15T09:30:00Z",
        "title": "SOC Analyst",
        "company": {"display_name": "BAE Systems"},
        "job_description": "Monitor SIEM alerts and investigate incidents.",
        "redirect_url": "https://example.com/jobs/998877",
    }
    msg1 = normalize_job_row(raw)
    msg2 = normalize_job_row(raw)
    assert msg1 is not None
    assert msg1 == msg2
    assert msg1["country"] == "GB"
    assert msg1["posted_date"] == "2026-10-15"
    assert msg1["source"] == "adzuna"
    assert msg1["company"] == "BAE Systems"
    contracts.validate("raw.jobs", msg1)


def test_replay_sample_dataset_in_event_time_order() -> None:
    sample_path = _repo_sample_path()
    producer = DryRunProducer()
    store = InMemoryPositionStore()

    agent = JobAdsAgent(
        seconds_per_day=0.0,
        checkpoint_interval=25,
        position_store=store,
    )
    last_pos = agent.replay_paths(sample_path, producer, dataset="jobs_sample")

    raw_jobs = [val for topic, _, val in producer.sent if topic == RAW_JOBS_TOPIC]
    assert len(raw_jobs) > 0
    assert last_pos == len(raw_jobs)

    # Verify global non-decreasing event-time order and country partitioning key
    dates = [msg["posted_date"] for msg in raw_jobs]
    assert dates == sorted(dates)
    for topic, key, val in producer.sent:
        if topic == RAW_JOBS_TOPIC:
            assert key == val["country"]

    saved = store.load("jobs_sample")
    assert saved is not None
    assert saved.row_number == len(raw_jobs)


def test_replay_csv_and_resume_from_checkpoint(tmp_path: Path) -> None:
    csv_file = tmp_path / "jobs.csv"
    csv_file.write_text(
        "id,source,country,posted_date,job_title,company,description\n"
        "1,adzuna,DE,2026-10-03,Security Engineer,Siemens,SIEM and SOAR engineering\n"
        "2,adzuna,UK,2026-10-01,SOC Analyst,BT,Incident triage in SOC\n"
        "3,adzuna,US,2026-10-02,US Analyst,Acme,Filtered out non-EU\n"
        "4,adzuna,FR,2026-10-02,,Orange,Missing title routed to errors.invalid\n",
        encoding="utf-8",
    )

    store = InMemoryPositionStore()
    # Simulate an earlier run that already checkpointed the first valid record (2026-10-01)
    first_msg = normalize_job_row(
        {
            "id": "2",
            "source": "adzuna",
            "country": "UK",
            "posted_date": "2026-10-01",
            "job_title": "SOC Analyst",
            "company": "BT",
            "description": "Incident triage in SOC",
        }
    )
    assert first_msg is not None
    store.save(
        ReplayCheckpoint(
            dataset="eu_jobs",
            row_number=1,
            event_date=date(2026, 10, 1),
            last_event_id=first_msg["event_id"],
        )
    )

    producer = DryRunProducer()
    agent = JobAdsAgent(seconds_per_day=0.0, position_store=store)
    agent.replay_paths(csv_file, producer, dataset="eu_jobs")

    emitted = [val for topic, _, val in producer.sent if topic == RAW_JOBS_TOPIC]
    assert len(emitted) == 1
    assert emitted[0]["country"] == "DE"
    assert emitted[0]["posted_date"] == "2026-10-03"


def test_cli_dry_run_on_sample_dataset(capsys) -> None:
    sample_path = str(_repo_sample_path())
    exit_code = main(["--input", sample_path, "--seconds-per-day", "0", "--dry-run"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Dry-run complete:" in captured.out