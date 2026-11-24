"""Kafka for stream stages: the connector JAR, the Kafka source and sink, and running a stage on Kafka.

Run a stage on the Kafka stack, e.g. S4 against the local Compose Kafka, from
the repository root or from streaming/:

    python -m ace.kafka s4_demand_frequency.stage:DemandFrequency

KAFKA_BOOTSTRAP_SERVERS sets the brokers (default localhost:9092). --bounded
processes what is in the input topics now and then stops.

PyFlink's Kafka source and sink run in Flink's Java side, so every job needs the
connector JAR. It is not on PyPI; this module downloads the pinned version from
Maven Central the first time it is needed, checks its SHA-1, and keeps it in
ACE_FLINK_JARS (default ~/.cache/ace/flink-jars). A Docker image can call
connector_jar() at build time so the container never downloads anything.

Version: 5.0.0-2.2 is the newest connector release (Maven Central, 2026-05). It
is built for Flink 2.1 and 2.2; there is no release for Flink 2.3 yet. It runs
with our apache-flink 2.3.0: a bounded Kafka source and a Kafka sink with a topic
selector were tested against the local Kafka stack. Update the three constants
together when a release for Flink 2.3 appears.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import os
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

from ace import contracts

CONNECTOR_VERSION = "5.0.0-2.2"
CONNECTOR_JAR = f"flink-sql-connector-kafka-{CONNECTOR_VERSION}.jar"
CONNECTOR_SHA1 = "7bf2ded1144cb8aece05adcaccbc81b2ac3ef339"
CONNECTOR_URL = (
    "https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/"
    f"{CONNECTOR_VERSION}/{CONNECTOR_JAR}"
)

DEFAULT_BOOTSTRAP_SERVERS = "localhost:9092"
CHECKPOINT_MS = 60_000      # how often an unbounded job saves its state and Kafka offsets


def jars_dir() -> Path:
    """Where downloaded Flink JARs are kept: ACE_FLINK_JARS, or ~/.cache/ace/flink-jars."""
    configured = os.environ.get("ACE_FLINK_JARS")
    return Path(configured) if configured else Path.home() / ".cache" / "ace" / "flink-jars"


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, open(target, "wb") as f:
        while chunk := response.read(1 << 20):
            f.write(chunk)


def connector_jar(download: Callable[[str, Path], None] = _download) -> Path:
    """The Kafka connector JAR on disk, downloaded and checked on first use.

    A file that is already there is used only if its SHA-1 matches; otherwise it
    is downloaded again. A download with the wrong SHA-1 is deleted and raises,
    so a broken or tampered JAR never reaches Flink.

    Several stages may start at once. Each download goes to its own temporary
    file in the same folder, is checked there, and only then replaces the JAR in
    one step, so no caller ever sees a half-written or unchecked file. The
    temporary file is removed whatever happens.
    """
    folder = jars_dir()
    jar = folder / CONNECTOR_JAR
    if _is_checked(jar):
        return jar
    folder.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f"{CONNECTOR_JAR}.", suffix=".part", dir=folder)
    os.close(handle)
    partial = Path(name)
    try:
        download(CONNECTOR_URL, partial)
        actual = _sha1(partial)
        if actual != CONNECTOR_SHA1:
            raise RuntimeError(
                f"{CONNECTOR_JAR} from {CONNECTOR_URL} has SHA-1 {actual}, expected {CONNECTOR_SHA1}"
            )
        try:
            os.replace(partial, jar)
        except PermissionError:
            # Windows refuses to replace a file another process has open. That
            # only happens when another stage has just put a JAR there: use it
            # if it checks out.
            if not _is_checked(jar):
                raise
    finally:
        partial.unlink(missing_ok=True)
    return jar


def _is_checked(jar: Path) -> bool:
    try:
        return _sha1(jar) == CONNECTOR_SHA1
    except FileNotFoundError:
        return False


def add_connector(env: Any) -> None:
    """Make the Kafka connector JAR available to a Flink job."""
    env.add_jars(connector_jar().as_uri())


def read_topics(env: Any, topics: Iterable[str], bootstrap_servers: str, group_id: str,
                bounded: bool = False) -> Any:
    """Kafka records of the given topics as (topic, JSON text) pairs, as ace.stage.build_flink takes them.

    Each topic gets its own Kafka source, so every record keeps the name of the
    topic it came from; stages with several inputs need it. An unknown topic
    name fails here, before the job starts.

    Unbounded (the default) starts from the group's committed offsets, or from
    the earliest record for a new group, and runs until the job is stopped.
    Bounded stops at the offsets that are the latest when the job starts, which
    tests and one-off backfills use.
    """
    from pyflink.common import Types, WatermarkStrategy
    from pyflink.common.serialization import SimpleStringSchema
    from pyflink.datastream.connectors.kafka import (
        KafkaOffsetResetStrategy,
        KafkaOffsetsInitializer,
        KafkaSource,
    )

    pair = Types.TUPLE([Types.STRING(), Types.STRING()])
    streams = []
    for name in topics:
        topic = contracts.topic(name).name
        builder = (KafkaSource.builder()
                   .set_bootstrap_servers(bootstrap_servers)
                   .set_topics(topic)
                   .set_group_id(group_id)
                   .set_starting_offsets(
                       KafkaOffsetsInitializer.committed_offsets(KafkaOffsetResetStrategy.EARLIEST))
                   .set_value_only_deserializer(SimpleStringSchema()))
        if bounded:
            builder.set_bounded(KafkaOffsetsInitializer.latest())
        # Event time and watermarks are assigned later, by build_flink. The uids tie
        # the saved offsets to the stage and topic, not to the operator's place in the job.
        records = (env.from_source(builder.build(), WatermarkStrategy.no_watermarks(), f"Kafka {topic}")
                   .uid(f"{group_id}/kafka-source/{topic}"))
        streams.append(records.map(lambda text, topic=topic: (topic, text), output_type=pair)
                       .uid(f"{group_id}/topic-name/{topic}"))
    if not streams:
        raise ValueError("read_topics needs at least one topic")
    first, *rest = streams
    return first.union(*rest) if rest else first


def write_topics(outputs: Any, bootstrap_servers: str, stage_name: str = "ace") -> Any:
    """Write ace.stage.build_flink's (topic, key, JSON text) outputs to Kafka, each to its own topic.

    The topic comes from each output, so one sink serves a stage's results and
    its errors.invalid records. Delivery is at least once: after a restart from
    a checkpoint a few results may be written twice, which the sink's upserts
    absorb.

    Records are written without a Kafka key. PyFlink can only serialise the whole
    record as the key, not one field of it. Nothing downstream reads the key:
    stages group by the record's own country field and the Cassandra sink ignores
    it. The agents, which publish the raw topics, still set the key.

    stage_name makes the operator uids unique when several stages share one job.
    """
    from pyflink.common import Row, Types
    from pyflink.common.serialization import SerializationSchema, SimpleStringSchema
    from pyflink.datastream.connectors.base import DeliveryGuarantee
    from pyflink.datastream.connectors.kafka import KafkaRecordSerializationSchema, KafkaSink
    from pyflink.java_gateway import get_gateway

    rows = (outputs.map(lambda output: Row(output[0], output[2]),
                        output_type=Types.ROW([Types.STRING(), Types.STRING()]))
            .uid(f"{stage_name}/kafka-rows"))
    # The topic selector picks the topic from column 0; this writes only column 1,
    # the JSON text. PyFlink has no Python class for it, so the Java one is used.
    value_only = SerializationSchema(
        get_gateway().jvm.org.apache.flink.python.util.PythonConnectorUtils
        .SecondColumnSerializationSchema(SimpleStringSchema()._j_serialization_schema))
    sink = (KafkaSink.builder()
            .set_bootstrap_servers(bootstrap_servers)
            .set_record_serializer(KafkaRecordSerializationSchema.builder()
                                   .set_topic_selector(lambda row: row[0])
                                   .set_value_serialization_schema(value_only)
                                   .build())
            .set_delivery_guarantee(DeliveryGuarantee.AT_LEAST_ONCE)
            .build())
    return rows.sink_to(sink).name("Kafka sink").uid(f"{stage_name}/kafka-sink")


def checkpoint_dir(stages: Any) -> str:
    """Where an unbounded job saves its checkpoints, as a file:// URI.

    One folder per job, named after its stages, under ACE_CHECKPOINT_DIR
    (default ~/.cache/ace/checkpoints). Without a folder Flink keeps
    checkpoints in memory, where a job's state may be at most 5 MB.
    """
    return _checkpoint_folder(stages).as_uri()


def _checkpoint_folder(stages: Any) -> Path:
    configured = os.environ.get("ACE_CHECKPOINT_DIR")
    base = Path(configured) if configured else Path.home() / ".cache" / "ace" / "checkpoints"
    # "_" joins the stage names: as_uri() would escape "+" as %2B, and Flink then
    # creates a folder literally named %2B, which latest_checkpoint() never finds.
    return (base / "_".join(stage.name for stage in _as_list(stages))).resolve()


def latest_checkpoint(stages: Any) -> str | None:
    """The newest complete checkpoint of this job, as a file:// URI, or None.

    Flink puts each run's checkpoints in <checkpoint_dir>/<run id>/chk-<n>, and
    writes chk-<n>/_metadata last, so a folder without it was never finished
    (for example, the container stopped while writing it).
    """
    finished = _checkpoint_folder(stages).glob("*/chk-*/_metadata")
    newest = max(finished, key=lambda metadata: metadata.stat().st_mtime, default=None)
    return newest.parent.as_uri() if newest else None


def _as_list(stages: Any) -> list[Any]:
    listed = list(stages) if isinstance(stages, (list, tuple)) else [stages]
    if not listed:
        raise ValueError("needs at least one stage")
    names = [stage.name for stage in listed]
    if len(set(names)) != len(names):
        raise ValueError(f"stage names must be unique in one job, got {names}")
    return listed


def build_job(stages: Any, bootstrap_servers: str, bounded: bool = False) -> Any:
    """A Flink job that runs one stage, or several side by side, between Kafka topics.

    Each stage reads its own input topics and writes its own outputs, exactly as
    when it runs alone. Running several stages in one job shares one Java
    process, which saves memory on a small machine.

    An unbounded job saves a checkpoint every CHECKPOINT_MS, with the stages'
    state and the Kafka offsets they have processed, to checkpoint_dir(), and
    starts from latest_checkpoint() when there is one. So a restarted job
    continues where it stopped, state included. Without a checkpoint each stage
    starts from its consumer group's committed offsets, with empty state.
    """
    from pyflink.common import Configuration

    from ace import stage as ace_stage

    stages = _as_list(stages)
    env = ace_stage.flink_env()
    add_connector(env)
    roots = {ace_stage._import_root(contracts.__name__)}
    roots.update(ace_stage._import_root(type(stage).__module__) for stage in stages)
    for root in roots:
        env.add_python_file(root)  # so Flink's Python workers can import the stages and ace
    if not bounded:
        env.enable_checkpointing(CHECKPOINT_MS)
        # Files, not the default in-memory storage: its 5 MB limit made every S8
        # checkpoint fail once S8 held one model per country.
        checkpoints = Configuration()
        checkpoints.set_string("execution.checkpointing.storage", "filesystem")
        checkpoints.set_string("execution.checkpointing.dir", checkpoint_dir(stages))
        # Keep the last checkpoint when the job stops, so the next start can use it.
        checkpoints.set_string("execution.checkpointing.externalized-checkpoint-retention",
                               "RETAIN_ON_CANCELLATION")
        restore = latest_checkpoint(stages)
        if restore:
            checkpoints.set_string("execution.state-recovery.path", restore)
            # The new run owns the checkpoint and deletes it once it has a newer one.
            checkpoints.set_string("execution.state-recovery.claim-mode", "CLAIM")
            # State of a stage no longer in this job is dropped instead of blocking
            # the start; the operator uids match the rest to their stages.
            checkpoints.set_string("execution.state-recovery.ignore-unclaimed-state", "true")
            print(f"Restoring from {restore}", flush=True)
        env.configure(checkpoints)
    for stage in stages:
        topics = stage.inputs if isinstance(stage, ace_stage.RecordStage) else (stage.input,)
        records = read_topics(env, topics, bootstrap_servers, group_id=stage.name, bounded=bounded)
        outputs, late = ace_stage.build_flink(stage, records)
        write_topics(outputs, bootstrap_servers, stage.name)
        if late is not None:
            # The late stream only counts late records (late_records_total); this sink
            # writes nothing but makes Flink run that count.
            late.filter(lambda _: False).uid(f"{stage.name}/late-drop").print().uid(f"{stage.name}/late-print")
    return env


def run_on_kafka(stages: Any, bootstrap_servers: str | None = None, bounded: bool = False) -> None:
    """Run one stage, or several in one job, on Kafka until stopped, or, if bounded, until the inputs are read."""
    stages = _as_list(stages)
    servers = bootstrap_servers or os.environ.get("KAFKA_BOOTSTRAP_SERVERS", DEFAULT_BOOTSTRAP_SERVERS)
    build_job(stages, servers, bounded).execute("ace " + ", ".join(stage.name for stage in stages))


def load_stage(spec: str, kwargs: dict[str, str] | None = None) -> Any:
    """'module:Class' -> a stage instance, e.g. 's4_demand_frequency.stage:DemandFrequency'.

    kwargs are passed to the class, for stages with constructor settings, e.g.
    {'period': 'daily'} for S7's daily branch.

    Stage packages live in streaming/. Run from the repository root, that folder
    is searched as well, so the same command works from the root and from streaming/.
    """
    module_name, sep, class_name = spec.partition(":")
    if not sep or not module_name or not class_name:
        raise ValueError(f"expected module:Class, got {spec!r}")
    streaming = str(Path.cwd() / "streaming")
    if Path(streaming).is_dir() and streaming not in sys.path:
        sys.path.append(streaming)
    return getattr(importlib.import_module(module_name), class_name)(**(kwargs or {}))


def _stage_arg(text: str) -> tuple[str, str]:
    name, sep, value = text.partition("=")
    if not sep or not name:
        raise argparse.ArgumentTypeError(f"expected NAME=VALUE, got {text!r}")
    return name, value


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m ace.kafka",
                                     description="Run a stream stage, or several in one job, on Kafka.")
    parser.add_argument("stages", nargs="+", metavar="stage",
                        help="module:Class, e.g. s4_demand_frequency.stage:DemandFrequency; "
                             "name several to run them in one job")
    parser.add_argument("--bounded", action="store_true",
                        help="process what is in the input topics now, then stop")
    parser.add_argument("--arg", dest="stage_args", type=_stage_arg, action="append", default=[],
                        metavar="NAME=VALUE",
                        help="a setting passed to the stage class, e.g. --arg period=daily; repeatable; "
                             "only with a single stage")
    args = parser.parse_args(argv)
    if args.stage_args and len(args.stages) > 1:
        parser.error("--arg works with a single stage only")
    stages = [load_stage(spec, dict(args.stage_args)) for spec in args.stages]
    run_on_kafka(stages, bounded=args.bounded)


if __name__ == "__main__":
    main()
