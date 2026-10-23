"""Stage base: everything a stream stage needs except its own algorithm.

A stage subclasses WindowedStage (S3, S4, S5, S7, S9, S10) or RecordStage
(S1, S2, S6, S8) and fills in a few methods. The base then:

- checks every input message against its schema; bad ones go to errors.invalid,
- uses the record's own date as event time, never arrival time,
- keeps a window open for the grace period after it ends, then publishes its
  result once; records that arrive after that are late, dropped and counted,
- checks every output message before it is published.

The same stage runs two ways:

- run_local(stage, records): plain Python over a list of records, following
  Flink's rules exactly. Fast and deterministic, so every stage test uses it.
- build_flink(stage, env, stream): the real Flink job (PyFlink DataStream API).

Flink advances its watermark only every 200 ms, so on a short test list it
never sees a record as late. run_local applies the watermark after every
record instead, which is why late-record tests use run_local.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from ace import contracts

LATE_METRIC = "late_records_total"
_DAY = timedelta(days=1)


@dataclass(frozen=True)
class Window:
    """One closed event-time window, start inclusive, end exclusive (UTC)."""

    start: datetime
    end: datetime

    @property
    def day(self) -> str:
        """The window's start date as YYYY-MM-DD, as used in agg.* messages."""
        return self.start.date().isoformat()

    @classmethod
    def from_ms(cls, start_ms: int, end_ms: int) -> "Window":
        return cls(_from_ms(start_ms), _from_ms(end_ms))


class WindowedStage:
    """A stage that sums records per key and window, e.g. counts per country and day.

    Fill in new(), add() and result(). memory in result() is a dict kept per
    key across windows (for 30- or 365-day views); leave it alone if not needed.
    State and memory must be picklable, because Flink saves them in checkpoints.
    """

    name: str                       # producer name, e.g. "s4-demand-count"
    input: str                      # input topic, from topics.csv
    output: str                     # output topic, from topics.csv
    time_field: str                 # field holding the event time, e.g. "posted_date"
    window: timedelta = _DAY
    grace: timedelta = timedelta(hours=1)

    def key(self, record: dict[str, Any]) -> str:
        """Which group a record belongs to; by default its Kafka key (the country)."""
        return contracts.key_of(self.input, record)

    def new(self) -> Any:
        """Empty state for a new window."""
        raise NotImplementedError

    def add(self, state: Any, record: dict[str, Any]) -> Any:
        """Add one record to the window's state and return the state."""
        raise NotImplementedError

    def result(self, key: str, window: Window, state: Any, memory: dict) -> list[dict[str, Any]]:
        """Messages to publish on self.output when a window closes."""
        raise NotImplementedError


class RecordStage:
    """A stage that handles one record at a time, e.g. dropping duplicates.

    Fill in process(). memory is a dict kept per key across records.
    """

    name: str
    inputs: tuple[str, ...]

    def key(self, topic: str, record: dict[str, Any]) -> str:
        return contracts.key_of(topic, record)

    def process(
        self, topic: str, record: dict[str, Any], memory: dict
    ) -> Iterable[tuple[str, dict[str, Any]]]:
        """(output topic, message) pairs to publish for this record."""
        raise NotImplementedError


def event_time_ms(value: str) -> int:
    """Event time in epoch milliseconds; a plain date means midnight UTC."""
    if len(value) == 10:
        moment = datetime.combine(date.fromisoformat(value), datetime.min.time(), timezone.utc)
    else:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def _from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def _ms(span: timedelta) -> int:
    return int(span.total_seconds() * 1000)


def _publish(stage_name: str, output: str, messages: Iterable[dict]) -> list[contracts.Outgoing]:
    return [contracts.route(output, m, producer=stage_name) for m in messages]


def _close_window(stage: WindowedStage, key: str, start_ms: int, end_ms: int,
                  state: Any, memory: dict) -> list[contracts.Outgoing]:
    messages = stage.result(key, Window.from_ms(start_ms, end_ms), state, memory)
    return _publish(stage.name, stage.output, messages)


# ---------------------------------------------------------------- local run


@dataclass
class RunResult:
    """What a local run published, and which records came too late."""

    outputs: list[contracts.Outgoing] = field(default_factory=list)
    late: list[dict[str, Any]] = field(default_factory=list)

    def published(self, topic: str) -> list[dict[str, Any]]:
        return [o.value for o in self.outputs if o.topic == topic]

    @property
    def invalid(self) -> list[dict[str, Any]]:
        return self.published(contracts.INVALID_TOPIC)


def run_local(stage: WindowedStage | RecordStage, records: Iterable[Any]) -> RunResult:
    """Run a stage over records in order, with Flink's event-time rules.

    records are messages for the stage's input topic, or (topic, message)
    pairs for a RecordStage with several inputs. At the end of the list every
    open window closes, as when a Flink job reaches the end of a bounded input.
    """
    if isinstance(stage, WindowedStage):
        return _run_windowed(stage, records)
    return _run_records(stage, records)


def _checked(topic: str, record: Any, producer: str, result: RunResult) -> bool:
    reason = contracts.check(topic, record)
    if reason is not None:
        invalid = contracts.invalid_record(topic, producer, reason, record)
        result.outputs.append(contracts.Outgoing(contracts.INVALID_TOPIC, topic, invalid))
    return reason is None


def _run_windowed(stage: WindowedStage, records: Iterable[dict]) -> RunResult:
    result = RunResult()
    size, grace = _ms(stage.window), _ms(stage.grace)
    open_windows: dict[tuple[int, str], Any] = {}
    memory: dict[str, dict] = {}
    watermark = float("-inf")

    def fire(until: float) -> None:
        # Flink fires a window once the watermark reaches its last millisecond.
        due = sorted((s, k) for (s, k) in open_windows if s + size - 1 <= until)
        for start, key in due:
            state = _roundtrip(open_windows.pop((start, key)))
            key_memory = memory.setdefault(key, {})
            result.outputs += _close_window(stage, key, start, start + size, state, key_memory)
            memory[key] = _roundtrip(key_memory)

    for record in records:
        if not _checked(stage.input, record, stage.name, result):
            continue
        ts = event_time_ms(record[stage.time_field])
        start = ts - ts % size
        if start + size - 1 <= watermark:
            result.late.append(record)
            continue
        key = stage.key(record)
        state = open_windows[(start, key)] if (start, key) in open_windows else stage.new()
        open_windows[(start, key)] = stage.add(state, record)
        # Flink's bounded-out-of-orderness watermark: latest event time - grace - 1 ms.
        watermark = max(watermark, ts - grace - 1)
        fire(watermark)
    fire(float("inf"))
    return result


def _run_records(stage: RecordStage, records: Iterable[Any]) -> RunResult:
    result = RunResult()
    memory: dict[str, dict] = {}
    for item in records:
        topic, record = item if isinstance(item, tuple) else (stage.inputs[0], item)
        if not _checked(topic, record, stage.name, result):
            continue
        key = stage.key(topic, record)
        key_memory = memory.setdefault(key, {})
        for out_topic, message in stage.process(topic, record, key_memory):
            result.outputs.append(contracts.route(out_topic, message, producer=stage.name))
        memory[key] = _roundtrip(key_memory)
    return result


def _roundtrip(value: Any) -> Any:
    """Pickle and unpickle, so a stage whose state Flink could not save fails in tests."""
    return pickle.loads(pickle.dumps(value))


# ---------------------------------------------------------------- Flink run


def flink_env(parallelism: int = 1):
    """A Flink environment whose Python workers use this same Python."""
    import sys

    from pyflink.datastream import StreamExecutionEnvironment

    _check_windows_paths(sys.executable)
    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(parallelism)
    env.set_python_executable(sys.executable)
    return env


def _check_windows_paths(python: str) -> None:
    """Fail early with a clear message where PyFlink's Windows start script would break.

    Flink starts its Python workers through pyflink-udf-runner.bat, which
    breaks on a Python path with spaces or brackets and on a PYTHONPATH with
    brackets; the worker then just dies with exit code 255.
    """
    import os

    if os.name != "nt":
        return
    if any(c in python for c in " ()"):
        raise RuntimeError(
            f"PyFlink cannot start its Python workers from {python} on Windows: the path has "
            "spaces or brackets. Create the virtual environment in a folder without them, "
            r"e.g. py -3.11 -m venv C:\venvs\ace"
        )
    if any(c in os.environ.get("PYTHONPATH", "") for c in "()"):
        raise RuntimeError(
            "PyFlink's Windows start script breaks on brackets in PYTHONPATH; "
            'install the package instead: pip install -e "shared/python[flink,dev]"'
        )


def build_flink(stage: WindowedStage | RecordStage, stream):
    """Add the stage to a Flink job.

    stream carries (topic, JSON text) pairs, as read from the input topics. Returns
    (outputs, late): outputs are (topic, key, JSON value) tuples ready for the
    Kafka sink, including errors.invalid messages; late are the late records,
    already counted in the late_records_total metric.
    """
    from pyflink.common import Duration, Time, Types, WatermarkStrategy
    from pyflink.common.watermark_strategy import TimestampAssigner
    from pyflink.datastream import OutputTag
    from pyflink.datastream.functions import (
        AggregateFunction,
        KeyedProcessFunction,
        MapFunction,
        ProcessFunction,
        ProcessWindowFunction,
    )
    from pyflink.datastream.state import ValueStateDescriptor
    from pyflink.datastream.window import TumblingEventTimeWindows

    pickled = Types.PICKLED_BYTE_ARRAY()
    out_type = Types.TUPLE([Types.STRING(), Types.STRING(), Types.STRING()])
    invalid_tag = OutputTag("invalid", out_type)
    name = stage.name

    def as_tuple(out: contracts.Outgoing):
        return out.topic, out.key, json.dumps(out.value, ensure_ascii=False)

    class CheckInput(ProcessFunction):
        def process_element(self, item, ctx):
            topic, text = item
            try:
                record = json.loads(text)
                reason = contracts.check(topic, record)
            except ValueError as error:
                record, reason = text, f"not valid JSON: {error}"
            if reason is None:
                yield topic, record
            else:
                bad = contracts.invalid_record(topic, name, reason, record)
                yield invalid_tag, as_tuple(contracts.Outgoing(contracts.INVALID_TOPIC, topic, bad))

    checked = stream.process(CheckInput(), output_type=pickled)
    invalid = checked.get_side_output(invalid_tag)

    if isinstance(stage, RecordStage):

        class Process(KeyedProcessFunction):
            def open(self, runtime_context):
                self.memory = runtime_context.get_state(ValueStateDescriptor("memory", Types.PICKLED_BYTE_ARRAY()))

            def process_element(self, item, ctx):
                topic, record = item
                memory = self.memory.value() or {}
                for out_topic, message in stage.process(topic, record, memory):
                    yield as_tuple(contracts.route(out_topic, message, producer=name))
                self.memory.update(memory)

        outputs = (checked.key_by(lambda item: stage.key(*item), key_type=Types.STRING())
                   .process(Process(), output_type=out_type))
        return outputs.union(invalid), None

    class EventTime(TimestampAssigner):
        def extract_timestamp(self, item, record_timestamp):
            return event_time_ms(item[1][stage.time_field])

    class Accumulate(AggregateFunction):
        def create_accumulator(self):
            return stage.new()

        def add(self, item, state):
            return stage.add(state, item[1])

        def get_result(self, state):
            return state

        def merge(self, a, b):
            raise NotImplementedError("tumbling windows never merge")

    class Closed(ProcessWindowFunction):
        def process(self, key, context, states):
            window = context.window()
            for state in states:
                yield key, window.start, window.end, state

    class Publish(KeyedProcessFunction):
        def open(self, runtime_context):
            self.memory = runtime_context.get_state(ValueStateDescriptor("memory", Types.PICKLED_BYTE_ARRAY()))

        def process_element(self, closed, ctx):
            key, start, end, state = closed
            memory = self.memory.value() or {}
            for out in _close_window(stage, key, start, end, state, memory):
                yield as_tuple(out)
            self.memory.update(memory)

    class CountLate(MapFunction):
        def open(self, runtime_context):
            self.late = runtime_context.get_metrics_group().counter(LATE_METRIC)

        def map(self, item):
            self.late.inc()
            return item

    late_tag = OutputTag("late", pickled)
    watermarks = (WatermarkStrategy
                  .for_bounded_out_of_orderness(Duration.of_millis(_ms(stage.grace)))
                  .with_timestamp_assigner(EventTime()))
    windows = (checked.assign_timestamps_and_watermarks(watermarks)
               .key_by(lambda item: stage.key(item[1]), key_type=Types.STRING())
               .window(TumblingEventTimeWindows.of(Time.milliseconds(_ms(stage.window))))
               .side_output_late_data(late_tag)
               .aggregate(Accumulate(), window_function=Closed(),
                          accumulator_type=pickled, output_type=pickled))
    outputs = (windows.key_by(lambda closed: closed[0], key_type=Types.STRING())
               .process(Publish(), output_type=out_type))
    late = windows.get_side_output(late_tag).map(CountLate(), output_type=pickled)
    return outputs.union(invalid), late


def run_flink_local(stage: WindowedStage | RecordStage, records: Iterable[Any]) -> list[contracts.Outgoing]:
    """Run a stage on a local Flink over a list of records and collect what it publishes."""
    from pyflink.common import Types

    env = flink_env()
    for root in {_import_root(type(stage).__module__), _import_root(contracts.__name__)}:
        env.add_python_file(root)  # so Flink's Python workers can import the stage and ace
    pairs = [item if isinstance(item, tuple) else (_first_input(stage), item) for item in records]
    texts = [(topic, json.dumps(record, ensure_ascii=False)) for topic, record in pairs]
    stream = env.from_collection(texts, type_info=Types.TUPLE([Types.STRING(), Types.STRING()]))
    outputs, _ = build_flink(stage, stream)
    with outputs.execute_and_collect() as results:
        return [contracts.Outgoing(t, k, json.loads(v)) for t, k, v in results]


def _import_root(module_name: str) -> str:
    """The folder a module is imported from, e.g. streaming/ for s4_demand_frequency.stage."""
    import sys
    from pathlib import Path

    file = Path(sys.modules[module_name].__file__).resolve()
    return str(file.parents[module_name.count(".")])


def _first_input(stage: WindowedStage | RecordStage) -> str:
    return stage.input if isinstance(stage, WindowedStage) else stage.inputs[0]
