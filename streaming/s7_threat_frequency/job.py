"""Compose S7's two branches without changing the shared stage framework."""

import json
from dataclasses import dataclass
from typing import Iterable

from ace import contracts
from ace.stage import RunResult, build_flink, run_local

from .stage import ThreatFrequencyStage


def _input_error(topic: str, key: str) -> bool:
    # ace routes invalid messages with the FAILED TOPIC as their Kafka key,
    # not the payload's country. A daily output error therefore has the key
    # agg.threats_daily, even when its bad country value is "threats.events".
    return topic == contracts.INVALID_TOPIC and key == contracts.topic("threats.events").name


@dataclass
class FrequencyResult:
    hourly: RunResult
    daily: RunResult

    @property
    def outputs(self) -> list[contracts.Outgoing]:
        # Both branches validate input. Publish its error once, from hourly;
        # preserve any daily OUTPUT validation failure for diagnosis.
        return self.hourly.outputs + [
            out for out in self.daily.outputs if not _input_error(out.topic, out.key)
        ]


def run_s7_local(records: Iterable[dict]) -> FrequencyResult:
    """Finite fixture run; both branches close remaining windows at EOF."""
    records = list(records)
    return FrequencyResult(
        run_local(ThreatFrequencyStage("hourly"), records),
        run_local(ThreatFrequencyStage("daily"), records),
    )


def build_s7(stream):
    """Build both branches on one (topic, JSON text) Flink stream.

    Return (outputs, late_hourly, late_daily). Outputs are the shared
    (topic, key, JSON value) tuples, ready for deployment's Kafka sink.
    Attach consumers to both late streams to execute their shared counters.
    """
    hourly, late_hourly = build_flink(ThreatFrequencyStage("hourly"), stream)
    daily, late_daily = build_flink(ThreatFrequencyStage("daily"), stream)
    daily = daily.filter(lambda item: not _input_error(item[0], item[1]))
    return hourly.union(daily), late_hourly, late_daily


def _stage_import_root():
    """Only streaming/ is needed for the canonical s7_threat_frequency import."""
    from pathlib import Path
    return Path(__file__).resolve().parent.parent


def run_s7_flink_local(records: Iterable[dict]) -> FrequencyResult:
    """Collect the combined graph, including its actual late side outputs.

    The shared run_flink_local is used by the per-branch tests. It currently
    accepts one stage and returns no late records, so this combined fixture
    harness is still needed until the shared runner supports both features.
    """

    from ace.stage import flink_env
    from pyflink.common import Types

    env = flink_env()
    env.add_python_file(str(_stage_import_root()))
    # ace is installed in the same Python environment used by flink_env's
    # workers. Never ship the repository, its virtualenv, or site-packages.
    topic = contracts.topic("threats.events").name
    stream = env.from_collection(
        [(topic, json.dumps(record, ensure_ascii=False)) for record in records],
        type_info=Types.TUPLE([Types.STRING(), Types.STRING()]),
    )
    outputs, late_hourly, late_daily = build_s7(stream)
    result = FrequencyResult(RunResult(), RunResult())
    daily_stage = ThreatFrequencyStage("daily")
    for kind, topic, key, value in _collect_fixture_streams(outputs, late_hourly, late_daily):
        record = json.loads(value)
        if kind != "output":
            getattr(result, kind).late.append(record)
        else:
            daily = topic == daily_stage.output or (
                topic == contracts.INVALID_TOPIC and record["producer"] == daily_stage.name
            )
            (result.daily if daily else result.hourly).outputs.append(contracts.Outgoing(topic, key, record))
    return result


def _collect_fixture_streams(outputs, late_hourly, late_daily):
    """Tag side outputs for collection, without printing record payloads."""
    from pyflink.common import Types

    row_type = Types.TUPLE([Types.STRING()] * 4)
    rows = outputs.map(lambda item: ("output", *item), output_type=row_type)
    for period, late in (("hourly", late_hourly), ("daily", late_daily)):
        rows = rows.union(late.map(
            lambda item, period=period: (period, item[0], "", json.dumps(item[1], ensure_ascii=False)),
            output_type=row_type,
        ))
    with rows.execute_and_collect() as results:
        yield from results
