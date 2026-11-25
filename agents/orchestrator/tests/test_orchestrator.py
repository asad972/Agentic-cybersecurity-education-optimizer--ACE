"""Tests for the ACE planning orchestrator (agents/orchestrator)."""

from __future__ import annotations

import json

from ace import contracts
from ace.llm import (
    BudgetExceededError,
    InMemoryLLMStore,
    LLMClient,
    LLMSettings,
    ProviderResponse,
)
from orchestrator.__main__ import main as cli_main
from orchestrator.agent import TASKS_COLLECT_TOPIC, DryRunProducer, OrchestratorAgent
from orchestrator.models import LIVE_JOB_COUNTRIES, CoverageSnapshot, ProposedTask
from orchestrator.planner import OrchestratorPlanner, sanitize_proposed_task


class _StubLocalProvider:
    def __init__(self, answer_json: str, *, name: str, model: str) -> None:
        self.name = name
        self.model = model
        self._answer_json = answer_json

    def generate(self, request) -> ProviderResponse:
        return ProviderResponse(
            answer=self._answer_json,
            input_tokens=120,
            output_tokens=60,
        )

    def close(self) -> None:
        return None


def _make_recorded_llm_client(recorded_answer: dict, *, budget_eur: str = "2") -> LLMClient:
    settings = LLMSettings.from_env(
        {
            "LLM_PROVIDER": "local",
            "LLM_DAILY_BUDGET_EUR": budget_eur,
            "LLM_MAX_TOKENS": "512",
        }
    )
    provider = _StubLocalProvider(
        json.dumps(recorded_answer),
        name=settings.provider,
        model=settings.local_model,
    )
    return LLMClient(settings=settings, store=InMemoryLLMStore(), provider=provider)


def test_rules_planner_prioritizes_low_coverage_countries_and_caps_at_five() -> None:
    snapshot = CoverageSnapshot(
        job_ads_by_country={"DE": 50, "FR": 40, "GB": 35, "IT": 25, "NL": 20, "PL": 1},
        programmes_by_country={"DE": 5},
        total_threats=0,
        platform_analyses_count=0,
    )
    planner = OrchestratorPlanner(use_llm=False)
    tasks = planner.plan(snapshot)

    assert len(tasks) == 5
    assert tasks[0]["agent"] == "job-agent"
    assert tasks[0]["action"] == "fetch_job_ads"
    assert tasks[0]["params"]["country"] == "AT"
    assert any(t["params"].get("country") == "PL" for t in tasks)

    for task in tasks:
        assert task["decided_by"] == "rules"
        assert len(task["reason"]) <= 300
        assert contracts.check("tasks.collect", task) is None


def test_llm_planner_uses_llm_client_and_refuses_disallowed_actions() -> None:
    recorded = {
        "tasks": [
            {
                "agent": "profile-agent",
                "action": "replay_profiles",
                "country": "DE",
                "reason": "Disallowed replay action that must be rejected.",
            },
            {
                "agent": "job-agent",
                "action": "fetch_job_ads",
                "country": "PL",
                "max_pages": 6,
                "reason": "Poland has low job ad coverage and needs fresh postings.",
            },
            {
                "agent": "education-agent",
                "action": "refresh_programmes",
                "country": "HU",
                "reason": "Hungary has no indexed cybersecurity programmes yet.",
            },
            {
                "agent": "threat-agent",
                "action": "refresh_threats",
                "hours": 12,
                "reason": "Threat feed refresh requested for the past 12 hours.",
            },
            {
                "agent": "platform-agent",
                "action": "rerun_platform_analysis",
                "reason": "Comparative platform analysis should be refreshed.",
            },
        ]
    }
    client = _make_recorded_llm_client(recorded)
    planner = OrchestratorPlanner(llm_client=client, use_llm=True)
    tasks = planner.plan(CoverageSnapshot())

    assert len(tasks) == 4
    assert [t["agent"] for t in tasks] == [
        "job-agent",
        "education-agent",
        "threat-agent",
        "platform-agent",
    ]
    assert tasks[0]["params"] == {"country": "PL", "max_pages": 6}
    assert tasks[1]["params"] == {"country": "HU"}
    assert tasks[2]["params"] == {"hours": 12}
    assert tasks[3]["params"] == {}
    for task in tasks:
        assert task["decided_by"] == "llm"
        assert contracts.check("tasks.collect", task) is None


def test_planner_falls_back_to_rules_when_llm_fails_or_exceeds_budget() -> None:
    class FailingLLMClient:
        def generate(self, **kwargs):
            raise BudgetExceededError("daily budget exhausted")

    planner = OrchestratorPlanner(llm_client=FailingLLMClient(), use_llm=True)
    tasks = planner.plan(CoverageSnapshot())

    assert 1 <= len(tasks) <= 5
    assert all(t["decided_by"] == "rules" for t in tasks)
    for task in tasks:
        assert contracts.check("tasks.collect", task) is None


def test_sanitize_refuses_invalid_country_and_mismatched_action() -> None:
    assert (
        sanitize_proposed_task(
            ProposedTask(agent="job-agent", action="fetch_job_ads", country="US", reason="Invalid US"),
            decided_by="llm",
            created_at="2026-11-25T12:00:00Z",
        )
        is None
    )
    assert (
        sanitize_proposed_task(
            ProposedTask(agent="job-agent", action="refresh_threats", country="DE", reason="Wrong action"),
            decided_by="llm",
            created_at="2026-11-25T12:00:00Z",
        )
        is None
    )


def test_coverage_snapshot_from_messages_and_agent_run_once() -> None:
    messages = [
        ("agg.demand_daily", {"country": c, "count": 25})
        for c in LIVE_JOB_COUNTRIES
        if c != "PL"
    ] + [
        ("agg.demand_daily", {"country": "PL", "count": 2}),
        ("agg.threats_daily", {"country": "DE", "count": 10}),
        ("raw.programmes", {"country": "DE"}),
        ("errors.invalid", {"topic": "raw.jobs"}),
    ]
    snapshot = CoverageSnapshot.from_messages(messages)
    assert snapshot.job_ads_by_country["PL"] == 2
    assert snapshot.total_threats == 10
    assert snapshot.invalid_by_topic == {"raw.jobs": 1}

    producer = DryRunProducer()
    agent = OrchestratorAgent(planner=OrchestratorPlanner(use_llm=False))
    emitted = agent.run_once(producer, snapshot=snapshot)

    assert emitted[0]["agent"] == "job-agent"
    assert emitted[0]["params"]["country"] == "PL"
    assert len(producer.sent) == len(emitted)
    for topic, key, value in producer.sent:
        assert topic == TASKS_COLLECT_TOPIC
        assert key == value["agent"]
        assert contracts.check("tasks.collect", value) is None


def test_cli_dry_run_exits_zero(capsys) -> None:
    rc = cli_main(["--dry-run", "--rules-only"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Dry-run complete: emitted 5 collect_task message(s)" in captured.out
