"""Tests for S1 near-duplicate job posting filter stage."""

from __future__ import annotations

import datetime
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


def test_s1_dedup_basic_flow_and_duplicate_metric() -> None:
    stage = S1DedupStage(threshold=0.75, lsh_threshold=0.6, window_days=30)
    res = run_local(stage, _sample_records())
    emitted = res.published(JOBS_CLEAN)
    emitted_ids = [r["event_id"] for r in emitted]

    assert emitted_ids == [
        "11111111-1111-1111-1111-111111111111",
        "33333333-3333-3333-3333-333333333333",
    ]
    assert stage.duplicates_removed == 1


def test_s1_dedup_window_eviction_and_future_date_protection() -> None:
    """Expired records are evicted from seen_ids, history, signatures, and LSH;
    neither late out-of-order ads nor wrong future dates break the window.
    """
    stage = S1DedupStage(threshold=0.75, lsh_threshold=0.6, window_days=30)
    memory: dict[str, object] = {}

    old_id = "11111111-1111-1111-1111-111111111111"
    future_id = "22222222-2222-2222-2222-222222222222"
    repost_id = "44444444-4444-4444-4444-444444444444"

    start_date = datetime.date(2026, 9, 1)
    old_ad = {
        "event_id": old_id,
        "source": "synthetic",
        "country": "DE",
        "posted_date": start_date.isoformat(),
        "job_title": "SOC Engineer",
        "company": "Allianz",
        "description": "Incident response and threat hunting in SOC team",
    }
    assert len(list(stage.process(RAW_JOBS, old_ad, memory))) == 1
    assert old_id in memory["seen_ids"]  # type: ignore[operator]
    assert old_id in memory["signatures"]  # type: ignore[operator]

    # Wrong future posted_date (> 1 day ahead) must not move latest_date or empty the window
    wrong_future_ad = {
        "event_id": future_id,
        "source": "synthetic",
        "country": "DE",
        "posted_date": "2099-01-01",
        "job_title": "Penetration Tester",
        "company": "BSI",
        "description": "Red team engagements and web app pentesting",
    }
    list(stage.process(RAW_JOBS, wrong_future_ad, memory))
    assert memory["latest_date"] == start_date
    assert old_id in memory["seen_ids"]  # type: ignore[operator]

    # Advance the stream day-by-day across 31 days
    for day_offset in range(1, 32):
        curr_date = start_date + datetime.timedelta(days=day_offset)
        daily_ad = {
            "event_id": f"00000000-0000-0000-0000-{day_offset:012d}",
            "source": "synthetic",
            "country": "DE",
            "posted_date": curr_date.isoformat(),
            "job_title": f"Role {day_offset}",
            "company": f"Company {day_offset}",
            "description": f"Unique description number {day_offset} for testing",
        }
        list(stage.process(RAW_JOBS, daily_ad, memory))

    # old_id (31 days old) must now be evicted from seen_ids, signatures, and LSH
    assert old_id not in memory["seen_ids"]  # type: ignore[operator]
    assert old_id not in memory["signatures"]  # type: ignore[operator]

    # Repost of old_ad text after leaving the 30-day window is accepted as new
    reposted_after_window = {
        "event_id": repost_id,
        "source": "synthetic",
        "country": "DE",
        "posted_date": (start_date + datetime.timedelta(days=31)).isoformat(),
        "job_title": "SOC Engineer",
        "company": "Allianz",
        "description": "Incident response and threat hunting in SOC team",
    }
    out = list(stage.process(RAW_JOBS, reposted_after_window, memory))
    assert len(out) == 1
    assert out[0][1]["event_id"] == repost_id


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
    assert stage.duplicates_removed == 0


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
    assert stage.duplicates_removed == 1


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
    assert "signatures" in loaded
    assert "lsh" in loaded
    assert "88888888-8888-8888-8888-888888888888" in loaded["seen_ids"]


@pytest.mark.flink
def test_s1_dedup_flink() -> None:
    """Run S1DedupStage through run_flink_local (which invokes open() via Process.open)."""
    pytest.importorskip("pyflink")
    records = _sample_records()
    local_res = run_local(S1DedupStage(threshold=0.75, lsh_threshold=0.6, window_days=30), records)
    flink_outs = run_flink_local(S1DedupStage(threshold=0.75, lsh_threshold=0.6, window_days=30), records)

    local_ids = [r["event_id"] for r in local_res.published(JOBS_CLEAN)]
    flink_ids = [out.value["event_id"] for out in flink_outs if out.topic == JOBS_CLEAN]
    assert sorted(flink_ids) == sorted(local_ids)