"""Tests for S2 Skill Extraction stage (taxonomy + ace.llm RecordedProvider)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from ace.llm import (
    BudgetExceededError,
    InMemoryLLMStore,
    LLMClient,
    LLMResult,
    LLMSettings,
    RecordedProvider,
)
from ace.stage import _roundtrip, run_flink_local, run_local
from s2_skill_extraction.llm_extractor import (
    ExtractedMentions,
    LLMSkillExtractor,
    MAX_MENTIONS,
)
from s2_skill_extraction.stage import (
    FALLBACK_METRIC,
    JOBS_CLEAN,
    RAW_PROFILES,
    SKILLS_DEMAND,
    SKILLS_SUPPLY,
    UNMAPPED_METRIC,
    S2SkillExtractionStage,
)

RECORDINGS_PATH = Path(__file__).with_name("recordings") / "s2_skill_extraction.json"


class _FakeCounter:
    def __init__(self) -> None:
        self.value = 0

    def inc(self, n: int = 1) -> None:
        self.value += n


class _FakeMetricsGroup:
    def __init__(self) -> None:
        self.counters: dict[str, _FakeCounter] = {}

    def counter(self, name: str) -> _FakeCounter:
        return self.counters.setdefault(name, _FakeCounter())


class _FakeRuntimeContext:
    def __init__(self) -> None:
        self.metrics = _FakeMetricsGroup()

    def get_metrics_group(self) -> _FakeMetricsGroup:
        return self.metrics


def _local_llm_settings() -> LLMSettings:
    return LLMSettings.from_env(
        {
            "LLM_PROVIDER": "local",
            "LLM_LOCAL_URL": "http://ollama:11434",
            "LLM_LOCAL_MODEL": "qwen3:4b",
            "LLM_MAX_TOKENS": "512",
            "LLM_MAX_ATTEMPTS": "1",
            "LLM_CACHE_POLL_SECONDS": "0.01",
        }
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
            "eBPF Runtime Security, and holding BTL1 (Blue Team Level 1)."
        ),
    }
    profile = {
        "event_id": "22222222-2222-2222-2222-222222222222",
        "source": "stackoverflow_survey",
        "country": "FR",
        "profile_date": "2026-10-02",
        "headline": "Penetration Tester",
        "text": (
            "Experienced in Internal Pivoting, Credential Harvesting, "
            "CloudTrail Forensics, and CRTO."
        ),
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


def test_s2_recorded_llm_extractor_emits_unmapped_skills_and_uses_cache() -> None:
    provider = RecordedProvider(
        RECORDINGS_PATH, provider="local", model="qwen3:4b"
    )
    store = InMemoryLLMStore()
    client = LLMClient(_local_llm_settings(), store, provider)
    stage = S2SkillExtractionStage(llm_client=client)
    ctx = _FakeRuntimeContext()
    stage.open(ctx)

    records = _sample_inputs()
    res = run_local(stage, records)

    assert len(res.invalid) == 0
    demand = res.published(SKILLS_DEMAND)
    supply = res.published(SKILLS_SUPPLY)
    assert len(demand) == 1
    assert len(supply) == 1

    assert "Intrusion Detection" in demand[0]["skills"]
    assert "Credential Dumping" in demand[0]["skills"]
    assert demand[0]["certifications"] == ["BTL1"]
    assert demand[0]["unmapped"] == ["eBPF Runtime Security"]
    assert demand[0]["ecsf_role"] == "Cyber Incident Responder"

    assert "Lateral Movement" in supply[0]["skills"]
    assert "Credential Dumping" in supply[0]["skills"]
    assert supply[0]["certifications"] == ["CRTO"]
    assert supply[0]["unmapped"] == ["CloudTrail Forensics"]
    assert ctx.metrics.counters[UNMAPPED_METRIC].value == 2
    assert provider.calls == 3

    res_cached = run_local(stage, records)
    assert len(res_cached.published(SKILLS_DEMAND)) == 1
    assert len(res_cached.published(SKILLS_SUPPLY)) == 1
    assert provider.calls == 3
    stage.close()


def test_s2_survives_pickle_roundtrip_with_use_llm_and_injected_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Stage configured with use_llm=True survives _roundtrip() before open().
    unopened = S2SkillExtractionStage(use_llm=True)
    assert unopened.extractor is None
    restored = _roundtrip(unopened)
    assert restored._use_llm is True
    assert restored.extractor is None

    provider = RecordedProvider(RECORDINGS_PATH, provider="local", model="qwen3:4b")
    client = LLMClient(_local_llm_settings(), InMemoryLLMStore(), provider)
    monkeypatch.setattr(
        LLMSkillExtractor,
        "from_env",
        classmethod(lambda cls, **kwargs: cls(client)),
    )

    ctx = _FakeRuntimeContext()
    restored.open(ctx)
    assert restored.extractor is not None

    res = run_local(restored, _sample_inputs()[:1])
    demand = res.published(SKILLS_DEMAND)
    assert len(demand) == 1
    assert demand[0]["unmapped"] == ["eBPF Runtime Security"]
    assert ctx.metrics.counters[UNMAPPED_METRIC].value == 1
    restored.close()

    # 2. Stage with an injected LLMClient (with InMemoryLLMStore) also survives _roundtrip().
    injected_provider = RecordedProvider(RECORDINGS_PATH, provider="local", model="qwen3:4b")
    injected_client = LLMClient(
        _local_llm_settings(), InMemoryLLMStore(), injected_provider
    )
    injected_stage = S2SkillExtractionStage(llm_client=injected_client)
    restored_injected = _roundtrip(injected_stage)
    res_injected = run_local(restored_injected, _sample_inputs()[:1])
    assert res_injected.published(SKILLS_DEMAND)[0]["unmapped"] == ["eBPF Runtime Security"]
    restored_injected.close()


def test_s2_open_falls_back_to_taxonomy_when_llm_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Hosted provider without LLM_API_KEY raises LLMConfigurationError inside from_env().
    monkeypatch.setenv("LLM_PROVIDER", "hosted")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    stage = S2SkillExtractionStage(use_llm=True)
    ctx = _FakeRuntimeContext()
    stage.open(ctx)

    assert stage.extractor is None
    assert stage._use_llm is False
    assert ctx.metrics.counters[FALLBACK_METRIC].value == 1

    res = run_local(stage, _sample_inputs()[:1])
    demand = res.published(SKILLS_DEMAND)
    assert len(demand) == 1
    assert "Intrusion Detection" in demand[0]["skills"]
    assert demand[0]["unmapped"] == []


def test_s2_llm_extractor_filters_hallucinations_caps_mentions_and_counts_fallbacks() -> None:
    class _NoisyClient:
        def generate(self, **kwargs):
            mentions = [
                "Suricata",
                "Quantum Blockchain AI",  # not in text -> dropped
                "x" * 120,  # exceeds MAX_MENTION_LENGTH -> dropped
                "eBPF Runtime Security",
            ] + [f"ExtraTerm{i}" for i in range(MAX_MENTIONS + 5)]
            return LLMResult(
                value=ExtractedMentions(mentions=mentions, ecsf_role=None),
                provider="local",
                model="qwen3:4b",
                cached=False,
                input_tokens=10,
                output_tokens=10,
                cost_eur=Decimal(0),
            )

        def close(self) -> None:
            pass

    noisy_stage = S2SkillExtractionStage(llm_client=_NoisyClient())  # type: ignore[arg-type]
    res = run_local(noisy_stage, _sample_inputs()[:1])
    demand = res.published(SKILLS_DEMAND)
    assert demand[0]["unmapped"] == ["eBPF Runtime Security"]

    class _FailingClient:
        def generate(self, **kwargs):
            raise BudgetExceededError("daily hosted budget exhausted")

        def close(self) -> None:
            pass

    failing_stage = S2SkillExtractionStage(llm_client=_FailingClient())  # type: ignore[arg-type]
    ctx = _FakeRuntimeContext()
    failing_stage.open(ctx)

    res_fallback = run_local(failing_stage, _sample_inputs()[:1])
    assert "Intrusion Detection" in res_fallback.published(SKILLS_DEMAND)[0]["skills"]
    assert res_fallback.published(SKILLS_DEMAND)[0]["unmapped"] == []
    assert ctx.metrics.counters[FALLBACK_METRIC].value == 1


@pytest.mark.flink
def test_s2_skill_extraction_flink() -> None:
    pytest.importorskip("pyflink")
    records = _sample_inputs()
    local_res = run_local(S2SkillExtractionStage(), records)
    flink_outs = run_flink_local(S2SkillExtractionStage(), records)

    local_pairs = sorted((o.topic, o.value["event_id"]) for o in local_res.outputs)
    flink_pairs = sorted((o.topic, o.value["event_id"]) for o in flink_outs)
    assert flink_pairs == local_pairs