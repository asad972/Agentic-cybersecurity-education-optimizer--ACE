"""Tests for the ACE planning orchestrator (agents/orchestrator)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from ace import contracts
from ace.llm import (
    BudgetExceededError,
    InMemoryLLMStore,
    LLMClient,
    LLMSettings,
    ProviderResponse,
)
from orchestrator.__main__ import main as cli_main
from orchestrator.agent import (
    TASKS_COLLECT_TOPIC,
    DryRunProducer,
    OrchestratorAgent,
    collect_coverage_from_kafka,
)
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


def test_agg_threats_sums_counts_map_values() -> None:
    threat_msg = {
        "country": "DE",
        "day": "2026-11-24",
        "counts": {"phishing": 40, "malware": 25},
    }
    assert contracts.check("agg.threats_daily", threat_msg) is None

    snapshot = CoverageSnapshot.from_messages(
        [("agg.threats_daily", threat_msg)],
        window_days=7,
        reference_date=date(2026, 11, 25),
    )
    assert snapshot.total_threats == 65
    assert snapshot.threats_by_country == {"DE": 65}

    planner = OrchestratorPlanner(use_llm=False)
    tasks = planner.plan(snapshot)
    assert all(t["agent"] != "threat-agent" for t in tasks)


def test_recent_window_filters_old_demand_daily_messages() -> None:
    old_msg = {"country": "PL", "day": "2026-11-01", "job_ads": 50, "counts": []}
    recent_msg = {"country": "PL", "day": "2026-11-24", "job_ads": 2, "counts": []}
    assert contracts.check("agg.demand_daily", old_msg) is None
    assert contracts.check("agg.demand_daily", recent_msg) is None

    records = [
        ("agg.demand_daily", {"country": c, "day": "2026-11-24", "job_ads": 25, "counts": []})
        for c in LIVE_JOB_COUNTRIES
        if c != "PL"
    ] + [
        ("agg.demand_daily", old_msg),
        ("agg.demand_daily", recent_msg),
    ]
    snapshot = CoverageSnapshot.from_messages(
        records,
        window_days=7,
        reference_date=date(2026, 11, 25),
    )
    assert snapshot.job_ads_by_country["PL"] == 2

    agent = OrchestratorAgent(planner=OrchestratorPlanner(use_llm=False))
    emitted = agent.run_once(DryRunProducer(), snapshot=snapshot)
    assert emitted[0]["agent"] == "job-agent"
    assert emitted[0]["params"]["country"] == "PL"


def test_collect_coverage_from_kafka_reads_to_end_offsets_without_message_cap() -> None:
    @dataclass
    class _Msg:
        value: dict

    class _FakeConsumer:
        def __init__(self) -> None:
            self.assigned = []
            self.offsets = {}
            self.closed = False
            self._data = {}

        def partitions_for_topic(self, topic: str):
            if topic == "agg.demand_daily":
                return {0, 1}
            return set()

        def assign(self, partitions) -> None:
            self.assigned = list(partitions)
            for tp in self.assigned:
                self.offsets[tp] = 0
                if tp.partition == 0:
                    self._data[tp] = [
                        _Msg({"country": "DE", "day": "2026-10-01", "job_ads": 100, "counts": []})
                        for _ in range(5500)
                    ]
                else:
                    self._data[tp] = [
                        _Msg({"country": "DE", "day": "2026-11-24", "job_ads": 7, "counts": []})
                    ]

        def seek_to_beginning(self, *partitions) -> None:
            for tp in partitions:
                self.offsets[tp] = 0

        def end_offsets(self, partitions):
            return {tp: len(self._data[tp]) for tp in partitions}

        def poll(self, timeout_ms: int = 1000):
            batch = {}
            for tp in self.assigned:
                pos = self.offsets[tp]
                msgs = self._data[tp][pos : pos + 2000]
                if msgs:
                    self.offsets[tp] = pos + len(msgs)
                    batch[tp] = msgs
            return batch

        def position(self, tp) -> int:
            return self.offsets[tp]

        def close(self) -> None:
            self.closed = True

    fake = _FakeConsumer()
    snapshot = collect_coverage_from_kafka(
        "localhost:9092",
        window_days=7,
        reference_date=date(2026, 11, 25),
        consumer_factory=lambda _: fake,
    )
    assert fake.closed is True
    assert snapshot.job_ads_by_country["DE"] == 7


def test_coverage_snapshot_from_messages_and_agent_run_once() -> None:
    messages = [
        ("agg.demand_daily", {"country": c, "day": "2026-11-24", "job_ads": 25, "counts": []})
        for c in LIVE_JOB_COUNTRIES
        if c != "PL"
    ] + [
        ("agg.demand_daily", {"country": "PL", "day": "2026-11-24", "job_ads": 2, "counts": []}),
        ("agg.threats_daily", {"country": "DE", "day": "2026-11-24", "counts": {"phishing": 10}}),
        ("raw.programmes", {"country": "DE"}),
        ("errors.invalid", {"topic": "raw.jobs", "detected_at": "2026-11-24T10:00:00Z"}),
    ]
    snapshot = CoverageSnapshot.from_messages(
        messages,
        window_days=7,
        reference_date=date(2026, 11, 25),
    )
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


def test_run_loop_logs_and_continues_after_transient_error() -> None:
    class _FlakyAgent(OrchestratorAgent):
        def __init__(self) -> None:
            super().__init__(planner=OrchestratorPlanner(use_llm=False))
            self.calls = 0

        def run_once(self, producer, snapshot=None, *, bootstrap_servers=None, poll_timeout_ms=1000):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient broker outage")
            return super().run_once(producer, snapshot=CoverageSnapshot())

    agent = _FlakyAgent()
    sleeps: list[float] = []
    cycles = agent.run_loop(
        DryRunProducer(),
        bootstrap_servers="localhost:9092",
        interval_seconds=60.0,
        max_cycles=2,
        sleep_fn=sleeps.append,
    )
    assert cycles == 2
    assert agent.calls == 2
    assert sleeps == [60.0]


def test_cli_dry_run_exits_zero(capsys) -> None:
    rc = cli_main(["--dry-run", "--rules-only"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Dry-run complete: emitted 5 collect_task message(s)" in captured.out
