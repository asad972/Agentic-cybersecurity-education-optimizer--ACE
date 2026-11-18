import json
from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, generate_latest

from ace_exporter.collector import PipelineCollector
from ace_exporter.counts import PipelineCounts
from ace_exporter.llm_spend import Spend, SpendState

NOW = datetime(2026, 11, 8, 14, 30, tzinfo=timezone.utc)


def registry_for(counts=None, state=None, budget=None):
    registry = CollectorRegistry()
    registry.register(PipelineCollector(counts or PipelineCounts(), lambda: state or SpendState(), budget))
    return registry


def add(counts, kafka_topic, **fields):
    counts.add(kafka_topic, json.dumps(fields).encode())


def test_counts_become_counters():
    counts = PipelineCounts()
    add(counts, "raw.jobs", source="adzuna")
    add(counts, "raw.jobs", source="adzuna")
    add(counts, "skills.demand", source="adzuna", skills=["SIEM"], unmapped=["Wazuh", "Splunk dashboards"])
    add(counts, "errors.invalid", topic="skills.demand", producer="s2-extract")
    add(counts, "tasks.collect", agent="job-agent", action="fetch_job_ads", decided_by="rules")
    counts.add("raw.jobs", b"not json")
    registry = registry_for(counts)

    value = registry.get_sample_value
    assert value("ace_records_total", {"topic": "raw.jobs", "source": "adzuna"}) == 2
    assert value("ace_skill_mentions_total", {"topic": "skills.demand", "kind": "unmapped"}) == 2
    assert value("ace_skill_mentions_total", {"topic": "skills.demand", "kind": "skill"}) == 1
    assert value("ace_invalid_messages_total", {"topic": "skills.demand", "producer": "s2-extract"}) == 1
    assert value("ace_collect_tasks_total",
                 {"agent": "job-agent", "action": "fetch_job_ads", "decided_by": "rules"}) == 1
    assert value("ace_unreadable_messages_total", {"topic": "raw.jobs"}) == 1


def test_mention_series_exist_before_any_message():
    registry = registry_for()
    for topic in ("skills.demand", "skills.supply"):
        for kind in ("skill", "certification", "unmapped"):
            assert registry.get_sample_value("ace_skill_mentions_total", {"topic": topic, "kind": kind}) == 0


def test_llm_spend_becomes_gauges():
    state = SpendState(True, True, {("skill_extraction", "hosted"): Spend(0.25, 4)}, NOW)
    registry = registry_for(state=state, budget=2.0)

    value = registry.get_sample_value
    labels = {"task": "skill_extraction", "provider": "hosted"}
    assert value("ace_llm_spend_today_eur", labels) == 0.25
    assert value("ace_llm_calls_today", labels) == 4
    assert value("ace_llm_daily_budget_eur") == 2.0
    assert value("ace_cassandra_up") == 1
    assert value("ace_llm_spend_available") == 1
    assert value("ace_llm_spend_checked_timestamp_seconds") == NOW.timestamp()


def test_before_the_first_spend_check_everything_reads_as_unavailable():
    registry = registry_for()
    assert registry.get_sample_value("ace_cassandra_up") == 0
    assert registry.get_sample_value("ace_llm_spend_available") == 0
    assert registry.get_sample_value("ace_llm_spend_checked_timestamp_seconds") is None
    assert registry.get_sample_value("ace_llm_daily_budget_eur") is None


def test_output_is_valid_prometheus_text():
    counts = PipelineCounts()
    add(counts, "raw.jobs", source='odd "source"\nname')
    text = generate_latest(registry_for(counts)).decode()
    assert '# TYPE ace_records_total counter' in text
    assert 'source="odd \\"source\\"\\nname"' in text
