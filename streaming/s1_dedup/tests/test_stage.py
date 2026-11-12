"""Tests for S1 near-duplicate job posting filter stage."""

from __future__ import annotations

import pickle

import pytest

from ace import contracts
from ace.stage import run_flink_local, run_local

from ..stage import S1DedupStage

RAW_JOBS = contracts.topic("raw.jobs").name
JOBS_CLEAN = contracts.topic("jobs.clean").name


def _sample_records() -> list[tuple[str, dict[str, str]]]:
    ad1 = {
        "event_id": "11111111-1111-1111-1111-111111111111",
        "source": "synthetic",
        "country": "DE",
        "posted_date": "2026-10-01",
        "job_title": "Security Analyst",
        "company": "Siemens",
        "description": "Looking for Security Analyst with SIEM Splunk experience",
    }
    # Near-duplicate (same country, slight punctuation edit)
    ad2 = {
        "event_id": "22222222-2222-2222-2222-222222222222",
        "source": "synthetic",
        "country": "DE",
        "posted_date": "2026-10-02",
        "job_title": "Security Analyst",
        "company": "Siemens",
        "description": "Looking for Security Analyst with SIEM, Splunk experience.",
    }
    # Distinct ad (same country)
    ad3 = {
        "event_id": "33333333-3333-3333-3333-333333333333",
        "source": "synthetic",
        "country": "DE",
        "posted_date": "2026-10-03",
        "job_title": "Cloud Architect",
        "company": "AWS",
        "description": "Design cloud architecture with Terraform and Kubernetes",
    }
    return [(RAW_JOBS, ad1), (RAW_JOBS, ad2), (RAW_JOBS, ad3)]


def test_s1_dedup_basic_flow() -> None:
    stage = S1DedupStage(threshold=0.75, window_days=30)
    res = run_local(stage, _sample_records())
    emitted = res.published(JOBS_CLEAN)
    emitted_ids = [r["event_id"] for r in emitted]

    assert emitted_ids == [
        "11111111-1111-1111-1111-111111111111",
        "33333333-3333-3333-3333-333333333333",
    ]


def test_s1_dedup_window_expiration() -> None:
    """Identical ad text posted after the 30-day window must be kept."""
    stage = S1DedupStage(threshold=0.75, window_days=30)
    old_ad = {
        "event_id": "11111111-1111-1111-1111-111111111111",
        "source": "synthetic",
        "country": "DE",
        "posted_date": "2026-09-01",
        "job_title": "SOC Engineer",
        "company": "Allianz",
        "description": "Incident response and threat hunting in SOC team",
    }
    reposted_after_window = {
        "event_id": "22222222-2222-2222-2222-222222222222",
        "source": "synthetic",
        "country": "DE",
        "posted_date": "2026-10-15",
        "job_title": "SOC Engineer",
        "company": "Allianz",
        "description": "Incident response and threat hunting in SOC team",
    }

    res = run_local(stage, [(RAW_JOBS, old_ad), (RAW_JOBS, reposted_after_window)])
    emitted_ids = [r["event_id"] for r in res.published(JOBS_CLEAN)]
    assert emitted_ids == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]


def test_s1_dedup_cross_country_independence() -> None:
    """Identical ads in different countries must both be kept."""
    stage = S1DedupStage(threshold=0.75)
    ad_de = {
        "event_id": "44444444-4444-4444-4444-444444444444",
        "source": "synthetic",
        "country": "DE",
        "posted_date": "2026-10-01",
        "job_title": "SOC Lead",
        "company": "CyberOrg",
        "description": "Incident response lead",
    }
    ad_fr = {
        "event_id": "55555555-5555-5555-5555-555555555555",
        "source": "synthetic",
        "country": "FR",
        "posted_date": "2026-10-01",
        "job_title": "SOC Lead",
        "company": "CyberOrg",
        "description": "Incident response lead",
    }

    res = run_local(stage, [(RAW_JOBS, ad_de), (RAW_JOBS, ad_fr)])
    emitted_ids = [r["event_id"] for r in res.published(JOBS_CLEAN)]
    assert emitted_ids == [
        "44444444-4444-4444-4444-444444444444",
        "55555555-5555-5555-5555-555555555555",
    ]


def test_s1_dedup_exact_id_repeat() -> None:
    stage = S1DedupStage()
    ad = {
        "event_id": "66666666-6666-6666-6666-666666666666",
        "source": "synthetic",
        "country": "ES",
        "posted_date": "2026-10-01",
        "job_title": "SecOps",
        "company": "A",
        "description": "Test",
    }

    res = run_local(stage, [(RAW_JOBS, ad), (RAW_JOBS, ad)])
    assert len(res.published(JOBS_CLEAN)) == 1


def test_s1_dedup_empty_text() -> None:
    """Ads with empty text should never be treated as duplicates."""
    stage = S1DedupStage()
    ad1 = {
        "event_id": "77777777-7777-7777-7777-777777777771",
        "source": "synthetic",
        "country": "IT",
        "posted_date": "2026-10-01",
        "job_title": "",
        "company": "",
        "description": "",
    }
    ad2 = {
        "event_id": "77777777-7777-7777-7777-777777777772",
        "source": "synthetic",
        "country": "IT",
        "posted_date": "2026-10-01",
        "job_title": "",
        "company": "",
        "description": "",
    }

    res = run_local(stage, [(RAW_JOBS, ad1), (RAW_JOBS, ad2)])
    assert len(res.published(JOBS_CLEAN)) == 2


def test_s1_dedup_memory_picklable() -> None:
    """Ensure stage memory (including MinHash + LSH state) is picklable."""
    stage = S1DedupStage()
    memory: dict[str, object] = {}
    ad = {
        "event_id": "88888888-8888-8888-8888-888888888888",
        "source": "synthetic",
        "country": "NL",
        "posted_date": "2026-10-10",
        "job_title": "Analyst",
        "company": "ING",
        "description": "Sample text",
    }

    list(stage.process(RAW_JOBS, ad, memory))

    loaded = pickle.loads(pickle.dumps(memory))
    assert "seen_ids" in loaded
    assert "lsh" in loaded
    assert "88888888-8888-8888-8888-888888888888" in loaded["seen_ids"]


@pytest.mark.flink
def test_s1_dedup_flink() -> None:
    """Run S1DedupStage on PyFlink local runtime and compare with run_local."""
    pytest.importorskip("pyflink")
    records = _sample_records()
    local_res = run_local(S1DedupStage(threshold=0.75, window_days=30), records)
    flink_res = run_flink_local(S1DedupStage(threshold=0.75, window_days=30), records)

    local_ids = [r["event_id"] for r in local_res.published(JOBS_CLEAN)]
    flink_ids = [r["event_id"] for r in flink_res.published(JOBS_CLEAN)]
    assert flink_ids == local_ids