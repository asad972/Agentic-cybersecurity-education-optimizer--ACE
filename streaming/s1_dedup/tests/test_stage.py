import datetime
import pickle
import pytest
from ace.stage import run_local
from s1_dedup.stage import S1DedupStage


def test_s1_dedup_basic_flow():
    stage = S1DedupStage(threshold=0.75, window_days=30)

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

    records = [
        ("raw.jobs", ad1),
        ("raw.jobs", ad2),
        ("raw.jobs", ad3),
    ]

    res = run_local(stage, records)
    emitted = res.published("jobs.clean")
    emitted_ids = [r["event_id"] for r in emitted]

    assert "11111111-1111-1111-1111-111111111111" in emitted_ids
    assert "22222222-2222-2222-2222-222222222222" not in emitted_ids
    assert "33333333-3333-3333-3333-333333333333" in emitted_ids


def test_s1_dedup_cross_country_independence():
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

    res = run_local(stage, [("raw.jobs", ad_de), ("raw.jobs", ad_fr)])
    emitted = res.published("jobs.clean")
    emitted_ids = [r["event_id"] for r in emitted]

    assert "44444444-4444-4444-4444-444444444444" in emitted_ids
    assert "55555555-5555-5555-5555-555555555555" in emitted_ids


def test_s1_dedup_exact_id_repeat():
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

    res = run_local(stage, [("raw.jobs", ad), ("raw.jobs", ad)])
    assert len(res.published("jobs.clean")) == 1


def test_s1_dedup_empty_text():
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

    res = run_local(stage, [("raw.jobs", ad1), ("raw.jobs", ad2)])
    assert len(res.published("jobs.clean")) == 2


def test_s1_dedup_memory_picklable():
    """Ensure stage memory is strictly picklable for Flink checkpoints."""
    stage = S1DedupStage()
    memory = {}
    ad = {
        "event_id": "88888888-8888-8888-8888-888888888888",
        "source": "synthetic",
        "country": "NL",
        "posted_date": "2026-10-10",
        "job_title": "Analyst",
        "company": "ING",
        "description": "Sample text",
    }

    list(stage.process("raw.jobs", ad, memory))

    dumped = pickle.dumps(memory)
    loaded = pickle.loads(dumped)
    assert "seen_ids" in loaded
    assert "88888888-8888-8888-8888-888888888888" in loaded["seen_ids"]


@pytest.mark.flink
def test_s1_dedup_flink():
    """Runs the stage through Flink runner if PyFlink is installed."""
    try:
        from ace.stage import run_flink
    except (ImportError, AttributeError):
        pytest.skip("Flink runtime not installed in local environment")

    stage = S1DedupStage(threshold=0.75)
    ad = {
        "event_id": "99999999-9999-9999-9999-999999999999",
        "source": "synthetic",
        "country": "DE",
        "posted_date": "2026-10-01",
        "job_title": "Engineer",
        "company": "BMW",
        "description": "Cloud security",
    }
    res = run_flink(stage, [("raw.jobs", ad)])
    assert len(res.published("jobs.clean")) == 1