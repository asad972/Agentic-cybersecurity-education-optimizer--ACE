"""Tests for the Job Ads replay agent (Issue #9)."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pytest

from ace import contracts
from ace.replay import InMemoryPositionStore, ReplayCheckpoint
from job_ads.__main__ import main
from job_ads.agent import RAW_JOBS_TOPIC, DryRunProducer, JobAdsAgent, KafkaJsonProducer
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


def test_normalize_huggingface_german_job_postings_row_and_dach_regions() -> None:
    hf_row = {
        "id": "10001-1003183488-S",
        "source": "bundesagentur",
        "source_url": "https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-1003183488-S",
        "posted_date": "2026-06-06",
        "title": "IT-Security Analyst (m/w/d)",
        "employer": "Siemens AG",
        "description_derived": None,
        "region": "Schleswig-Holstein",
        "nuts_code": "DEF",
        "esco_occupation": {
            "uri": "http://data.europa.eu/esco/occupation/example",
            "label": "information security analyst",
        },
        "esco_skills": [
            {"uri": "http://data.europa.eu/esco/skill/1", "label": "intrusion detection"},
            {"uri": "http://data.europa.eu/esco/skill/2", "label": "incident response"},
        ],
    }
    msg = normalize_job_row(hf_row)
    assert msg is not None
    assert msg["country"] == "DE"
    assert msg["source"] == "bundesagentur:esco_tags"
    assert msg["company"] == "Siemens AG"
    assert msg["url"] == "https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-1003183488-S"
    assert "information security analyst" in msg["description"]
    assert "intrusion detection" in msg["description"]
    contracts.validate("raw.jobs", msg)

    # Rows with null nuts_code map Austrian and Swiss regions to AT and CH
    at_msg = normalize_job_row(dict(hf_row, nuts_code=None, region="Oberösterreich"))
    assert at_msg is not None
    assert at_msg["country"] == "AT"

    ch_msg = normalize_job_row(dict(hf_row, nuts_code=None, region="Nordwestschweiz (mit Basel)"))
    assert ch_msg is not None
    assert ch_msg["country"] == "CH"

    # Rows with null nuts_code and missing/unknown region are skipped (None)
    assert normalize_job_row(dict(hf_row, nuts_code=None, region=None)) is None
    assert normalize_job_row(dict(hf_row, nuts_code=None, region="Sonstiges")) is None


def test_normalize_rejects_invalid_date_and_distinguishes_ads_without_id_or_url() -> None:
    with pytest.raises(ValueError, match="invalid posted_date"):
        normalize_job_row(
            {
                "country": "DE",
                "posted_date": "19.11.2026",
                "title": "SOC Analyst",
                "description": "Monitor alerts",
            }
        )

    ad1 = normalize_job_row(
        {
            "country": "DE",
            "posted_date": "2026-10-01",
            "title": "Security Engineer",
            "company": "Company A",
            "description": "Cloud security architecture role",
        }
    )
    ad2 = normalize_job_row(
        {
            "country": "DE",
            "posted_date": "2026-10-01",
            "title": "Security Engineer",
            "company": "Company B",
            "description": "Network firewall engineering role",
        }
    )
    assert ad1 is not None and ad2 is not None
    assert ad1["event_id"] != ad2["event_id"]


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
    assert agent.last_skipped_count == 0

    dates = [msg["posted_date"] for msg in raw_jobs]
    assert dates == sorted(dates)
    for topic, key, val in producer.sent:
        if topic == RAW_JOBS_TOPIC:
            assert key == val["country"]

    saved = store.load("jobs_sample")
    assert saved is not None
    assert saved.row_number == len(raw_jobs)


def test_replay_csv_logs_skipped_rows_and_resumes_from_checkpoint(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    csv_file = tmp_path / "jobs.csv"
    csv_file.write_text(
        "id,source,country,posted_date,job_title,company,description\n"
        "1,adzuna,DE,2026-10-03,Security Engineer,Siemens,SIEM and SOAR engineering\n"
        "2,adzuna,UK,2026-10-01,SOC Analyst,BT,Incident triage in SOC\n"
        "3,adzuna,US,2026-10-02,US Analyst,Acme,Filtered out non-EU\n"
        "4,adzuna,Deutchland,2026-10-02,Typo Country,Acme,Filtered out unknown country code\n"
        "5,adzuna,FR,2026-10-02,,Orange,Missing title routed to errors.invalid\n",
        encoding="utf-8",
    )

    store = InMemoryPositionStore()
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
    with caplog.at_level(logging.WARNING):
        agent.replay_paths(csv_file, producer, dataset="eu_jobs")

    emitted = [val for topic, _, val in producer.sent if topic == RAW_JOBS_TOPIC]
    assert len(emitted) == 1
    assert emitted[0]["country"] == "DE"
    assert emitted[0]["posted_date"] == "2026-10-03"
    assert agent.last_skipped_count == 2
    assert "Skipped 2 row(s) outside European Skills Scope" in caplog.text


def test_kafka_json_producer_flushes_before_close() -> None:
    calls: list[str] = []

    class _FakeKafka:
        def send(self, topic: str, key: str | None = None, value: object = None) -> None:
            calls.append("send")

        def flush(self) -> int:
            calls.append("flush")
            return 0

        def close(self) -> None:
            calls.append("close")

    wrapper = KafkaJsonProducer("localhost:9092", producer=_FakeKafka())
    wrapper.close()
    assert calls == ["flush", "close"]


def test_cli_dry_run_on_sample_dataset(capsys) -> None:
    sample_path = str(_repo_sample_path())
    exit_code = main(["--input", sample_path, "--seconds-per-day", "0", "--dry-run"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Dry-run complete:" in captured.out
    assert "0 skipped" in captured.out