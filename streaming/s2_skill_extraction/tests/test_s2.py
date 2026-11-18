"""Tests for S2 Skill Extraction stage."""

from __future__ import annotations

import pytest

from ace import contracts
from ace.stage import run_flink_local, run_local

from ..stage import (
    JOBS_CLEAN,
    RAW_PROFILES,
    SKILLS_DEMAND,
    SKILLS_SUPPLY,
    S2SkillExtractionStage,
)


def _sample_inputs() -> list[tuple[str, dict[str, str]]]:
    job_ad = {
        "event_id": "11111111-1111-1111-1111-111111111111",
        "source": "adzuna",
        "country": "DE",
        "posted_date": "2026-10-01",
        "job_title": "Cyber Incident Responder",
        "company": "Siemens",
        "description": (
            "Looking for a responder skilled in Suricata, Pass-the-Hash detection, "
            "and holding BTL1 (Blue Team Level 1)."
        ),
    }
    profile = {
        "event_id": "22222222-2222-2222-2222-222222222222",
        "source": "stackoverflow_survey",
        "country": "FR",
        "profile_date": "2026-10-02",
        "headline": "Penetration Tester",
        "text": "Experienced in Internal Pivoting, Credential Harvesting, and CRTO.",
    }
    unrelated_job = {
        "event_id": "33333333-3333-3333-3333-333333333333",
        "source": "adzuna",
        "country": "DE",
        "posted_date": "2026-10-03",
        "job_title": "Office Receptionist",
        "company": "LocalCorp",
        "description": "Answering phone calls and scheduling front-desk appointments.",
    }
    return [
        (JOBS_CLEAN, job_ad),
        (RAW_PROFILES, profile),
        (JOBS_CLEAN, unrelated_job),
    ]


def test_s2_extracts_demand_and_supply_and_skips_empty_skills() -> None:
    stage = S2SkillExtractionStage()
    res = run_local(stage, _sample_inputs())

    demand = res.published(SKILLS_DEMAND)
    supply = res.published(SKILLS_SUPPLY)
    assert len(res.invalid) == 0
    assert len(demand) == 1
    assert len(supply) == 1

    d_msg = demand[0]
    assert d_msg["event_id"] == "11111111-1111-1111-1111-111111111111"
    assert d_msg["country"] == "DE"
    assert "Intrusion Detection" in d_msg["skills"]
    assert "Credential Dumping" in d_msg["skills"]
    assert d_msg["certifications"] == ["BTL1"]
    assert d_msg["ecsf_role"] == "Cyber Incident Responder"

    s_msg = supply[0]
    assert s_msg["event_id"] == "22222222-2222-2222-2222-222222222222"
    assert s_msg["country"] == "FR"
    assert "Lateral Movement" in s_msg["skills"]
    assert "Credential Dumping" in s_msg["skills"]
    assert s_msg["certifications"] == ["CRTO"]
    assert s_msg["ecsf_role"] == "Penetration Tester"


def test_s2_custom_extractor_tracks_unmapped_mentions() -> None:
    def fake_extractor(topic, record, taxonomy):
        return taxonomy.classify_mentions(
            ["Suricata", "BTL2", "NewZeroDayFramework"],
            title=record.get("job_title", ""),
        )

    stage = S2SkillExtractionStage(extractor=fake_extractor)
    job_ad = _sample_inputs()[0][1]
    res = run_local(stage, [(JOBS_CLEAN, job_ad)])

    demand = res.published(SKILLS_DEMAND)
    assert len(demand) == 1
    assert demand[0]["skills"] == ["Intrusion Detection"]
    assert demand[0]["certifications"] == ["BTL2"]
    assert demand[0]["unmapped"] == ["NewZeroDayFramework"]
    assert stage.unmapped_count == 1


@pytest.mark.flink
def test_s2_skill_extraction_flink() -> None:
    pytest.importorskip("pyflink")
    records = _sample_inputs()
    local_res = run_local(S2SkillExtractionStage(), records)
    flink_outs = run_flink_local(S2SkillExtractionStage(), records)

    local_pairs = sorted((o.topic, o.value["event_id"]) for o in local_res.outputs)
    flink_pairs = sorted((o.topic, o.value["event_id"]) for o in flink_outs)
    assert flink_pairs == local_pairs