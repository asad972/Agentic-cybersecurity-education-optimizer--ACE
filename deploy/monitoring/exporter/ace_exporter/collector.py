"""Turns the exporter's counts and the LLM spend check into Prometheus metrics.

Metrics (all prefixed ace_):

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| ace_records_total | counter | topic, source | Records read per topic and source |
| ace_skill_mentions_total | counter | topic, kind | Skill, Certification and Unmapped mentions in skills.demand / skills.supply |
| ace_invalid_messages_total | counter | topic, producer | Messages on errors.invalid, by the topic they were meant for |
| ace_collect_tasks_total | counter | agent, action, decided_by | Orchestrator tasks on tasks.collect |
| ace_unreadable_messages_total | counter | topic | Messages the exporter could not read (not a JSON object) |
| ace_llm_spend_today_eur | gauge | task, provider | LLM spend since midnight UTC, from llm_cache |
| ace_llm_calls_today | gauge | task, provider | Paid-for (not cached) LLM answers since midnight UTC |
| ace_llm_daily_budget_eur | gauge | | LLM_DAILY_BUDGET_EUR, when set |
| ace_cassandra_up | gauge | | 1 when the last spend check reached Cassandra |
| ace_llm_spend_available | gauge | | 1 when the last spend check could read llm_cache |
| ace_llm_spend_checked_timestamp_seconds | gauge | | When the spend was last checked |
"""

from __future__ import annotations

from typing import Callable, Iterator

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from ace_exporter.counts import EXTRACTED, PipelineCounts
from ace_exporter.llm_spend import SpendState

MENTION_KINDS = ("skill", "certification", "unmapped")


class PipelineCollector(Collector):
    def __init__(self, counts: PipelineCounts, spend: Callable[[], SpendState],
                 daily_budget_eur: float | None = None):
        self.counts = counts
        self.spend = spend
        self.daily_budget_eur = daily_budget_eur

    def collect(self) -> Iterator[Metric]:
        snap = self.counts.snapshot()

        records = CounterMetricFamily("ace_records", "Records read per topic and source", labels=["topic", "source"])
        for (topic, source), value in sorted(snap.records.items()):
            records.add_metric([topic, source], value)
        yield records

        mentions = CounterMetricFamily("ace_skill_mentions", "Skill, Certification and Unmapped mentions per topic",
                                       labels=["topic", "kind"])
        # Always present, so the Unmapped share has a series before S2 publishes anything.
        for topic in EXTRACTED:
            for kind in MENTION_KINDS:
                mentions.add_metric([topic, kind], snap.mentions.get((topic, kind), 0))
        yield mentions

        invalid = CounterMetricFamily("ace_invalid_messages", "Messages on errors.invalid per target topic and producer",
                                      labels=["topic", "producer"])
        for (topic, producer), value in sorted(snap.invalid.items()):
            invalid.add_metric([topic, producer], value)
        yield invalid

        tasks = CounterMetricFamily("ace_collect_tasks", "Orchestrator tasks on tasks.collect",
                                    labels=["agent", "action", "decided_by"])
        for key, value in sorted(snap.tasks.items()):
            tasks.add_metric(list(key), value)
        yield tasks

        unreadable = CounterMetricFamily("ace_unreadable_messages", "Messages the exporter could not read",
                                         labels=["topic"])
        for topic, value in sorted(snap.unreadable.items()):
            unreadable.add_metric([topic], value)
        yield unreadable

        yield from self._spend_metrics(self.spend())

    def _spend_metrics(self, state: SpendState) -> Iterator[Metric]:
        eur = GaugeMetricFamily("ace_llm_spend_today_eur", "LLM spend since midnight UTC, from llm_cache",
                                labels=["task", "provider"])
        calls = GaugeMetricFamily("ace_llm_calls_today", "Paid-for LLM answers since midnight UTC, from llm_cache",
                                  labels=["task", "provider"])
        for (task, provider), spend in sorted(state.by_task.items()):
            eur.add_metric([task, provider], spend.eur)
            calls.add_metric([task, provider], spend.calls)
        yield eur
        yield calls
        if self.daily_budget_eur is not None:
            yield GaugeMetricFamily("ace_llm_daily_budget_eur", "LLM_DAILY_BUDGET_EUR", value=self.daily_budget_eur)
        yield GaugeMetricFamily("ace_cassandra_up", "1 when the last spend check reached Cassandra",
                                value=int(state.cassandra_up))
        yield GaugeMetricFamily("ace_llm_spend_available", "1 when the last spend check could read llm_cache",
                                value=int(state.available))
        if state.checked_at is not None:
            yield GaugeMetricFamily("ace_llm_spend_checked_timestamp_seconds", "When the LLM spend was last checked",
                                    value=state.checked_at.timestamp())
