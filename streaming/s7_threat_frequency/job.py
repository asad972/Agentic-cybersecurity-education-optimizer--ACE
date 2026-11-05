"""Compose S7's two branches without changing the shared stage framework."""

import json
from dataclasses import dataclass
from typing import Iterable

from ace import contracts
from ace.stage import RunResult, build_flink, run_local

from .stage import ThreatFrequencyStage


def _input_error(topic: str, value: dict) -> bool:
    return topic == contracts.INVALID_TOPIC and value["topic"] == contracts.topic("threats.events").name


@dataclass
class FrequencyResult:
    hourly: RunResult
    daily: RunResult

    @property
    def outputs(self) -> list[contracts.Outgoing]:
        # Both branches validate input. Publish its error once, from hourly;
        # preserve any daily OUTPUT validation failure for diagnosis.
        return self.hourly.outputs + [
            out for out in self.daily.outputs if not _input_error(out.topic, out.value)
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
    daily = daily.filter(lambda item: not _input_error(item[0], json.loads(item[2])))
    return hourly.union(daily), late_hourly, late_daily


def run_s7_flink_local(records: Iterable[dict]) -> list[contracts.Outgoing]:
    """Exercise the actual combined PyFlink graph on bounded fake records."""
    from pathlib import Path

    from ace.stage import flink_env
    from pyflink.common import Types

    env = flink_env()
    root = Path(__file__).resolve().parents[2]
    env.add_python_file(str(root))
    # Ship source only. An installed ace package lives in site-packages;
    # shipping that entire directory also copies PyFlink's worker launcher.
    env.add_python_file(str(root / "shared" / "python"))
    topic = contracts.topic("threats.events").name
    stream = env.from_collection(
        [(topic, json.dumps(record)) for record in records],
        type_info=Types.TUPLE([Types.STRING(), Types.STRING()]),
    )
    outputs, late_hourly, late_daily = build_s7(stream)
    # Consume late side outputs, including their late_records_total counters.
    late_hourly.print("s7-late-hourly")
    late_daily.print("s7-late-daily")
    with outputs.execute_and_collect() as results:
        return [contracts.Outgoing(topic, key, json.loads(value)) for topic, key, value in results]
