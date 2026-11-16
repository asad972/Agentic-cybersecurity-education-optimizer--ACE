"""Kafka for stream stages: the Flink Kafka connector JAR, the Kafka source and the Kafka sink.

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

import hashlib
import os
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
    """
    folder = jars_dir()
    jar = folder / CONNECTOR_JAR
    if jar.exists() and _sha1(jar) == CONNECTOR_SHA1:
        return jar
    folder.mkdir(parents=True, exist_ok=True)
    partial = jar.with_name(jar.name + ".part")
    download(CONNECTOR_URL, partial)
    actual = _sha1(partial)
    if actual != CONNECTOR_SHA1:
        partial.unlink()
        raise RuntimeError(
            f"{CONNECTOR_JAR} from {CONNECTOR_URL} has SHA-1 {actual}, expected {CONNECTOR_SHA1}"
        )
    partial.replace(jar)
    return jar


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
        # Event time and watermarks are assigned later, by build_flink.
        records = env.from_source(builder.build(), WatermarkStrategy.no_watermarks(), f"Kafka {topic}")
        streams.append(records.map(lambda text, topic=topic: (topic, text), output_type=pair))
    if not streams:
        raise ValueError("read_topics needs at least one topic")
    first, *rest = streams
    return first.union(*rest) if rest else first


def write_topics(outputs: Any, bootstrap_servers: str) -> Any:
    """Write ace.stage.build_flink's (topic, key, JSON text) outputs to Kafka, each to its own topic.

    The topic comes from each output, so one sink serves a stage's results and
    its errors.invalid records. Delivery is at least once: after a restart from
    a checkpoint a few results may be written twice, which the sink's upserts
    absorb.

    Records are written without a Kafka key. PyFlink can only serialise the whole
    record as the key, not one field of it. Nothing downstream reads the key:
    stages group by the record's own country field and the Cassandra sink ignores
    it. The agents, which publish the raw topics, still set the key.
    """
    from pyflink.common import Row, Types
    from pyflink.common.serialization import SerializationSchema, SimpleStringSchema
    from pyflink.datastream.connectors.base import DeliveryGuarantee
    from pyflink.datastream.connectors.kafka import KafkaRecordSerializationSchema, KafkaSink
    from pyflink.java_gateway import get_gateway

    rows = outputs.map(lambda output: Row(output[0], output[2]),
                       output_type=Types.ROW([Types.STRING(), Types.STRING()]))
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
    return rows.sink_to(sink).name("Kafka sink")
