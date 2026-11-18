"""The Kafka connector JAR is downloaded once, checked, and reused. No network in these tests."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ace import kafka
from ace.stage import WindowedStage


@pytest.fixture
def jars(tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_FLINK_JARS", str(tmp_path))
    return tmp_path


def fake_download(content):
    """A download that writes content and records the URLs it was asked for."""
    calls = []

    def download(url, target):
        calls.append(url)
        target.write_bytes(content)

    download.calls = calls
    return download


@pytest.fixture
def pinned(monkeypatch):
    """Pin the expected SHA-1 to that of a small fake JAR."""
    content = b"fake connector jar"
    monkeypatch.setattr(kafka, "CONNECTOR_SHA1", hashlib.sha1(content).hexdigest())
    return content


def test_downloads_once_then_reuses_the_checked_file(jars, pinned):
    download = fake_download(pinned)
    first = kafka.connector_jar(download)
    second = kafka.connector_jar(download)
    assert first == second == jars / kafka.CONNECTOR_JAR
    assert first.read_bytes() == pinned
    assert download.calls == [kafka.CONNECTOR_URL]


def test_wrong_checksum_is_rejected_and_nothing_is_kept(jars, pinned):
    with pytest.raises(RuntimeError, match="expected"):
        kafka.connector_jar(fake_download(b"tampered"))
    assert list(jars.iterdir()) == []


def test_a_damaged_cached_jar_is_downloaded_again(jars, pinned):
    (jars / kafka.CONNECTOR_JAR).write_bytes(b"half a jar")
    download = fake_download(pinned)
    assert kafka.connector_jar(download).read_bytes() == pinned
    assert len(download.calls) == 1


def test_default_folder_is_in_the_home_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("ACE_FLINK_JARS", raising=False)
    monkeypatch.setattr(kafka.Path, "home", lambda: tmp_path)
    assert kafka.jars_dir() == tmp_path / ".cache" / "ace" / "flink-jars"


def test_url_and_file_name_match_the_pinned_version():
    assert kafka.CONNECTOR_JAR == f"flink-sql-connector-kafka-{kafka.CONNECTOR_VERSION}.jar"
    assert kafka.CONNECTOR_URL.endswith(f"/{kafka.CONNECTOR_VERSION}/{kafka.CONNECTOR_JAR}")


def test_read_topics_needs_known_topics():
    with pytest.raises(KeyError):
        kafka.read_topics(env=None, topics=["no.such.topic"], bootstrap_servers="x", group_id="g")
    with pytest.raises(ValueError, match="at least one topic"):
        kafka.read_topics(env=None, topics=[], bootstrap_servers="x", group_id="g")


@pytest.mark.flink
def test_read_topics_gives_one_kafka_source_per_topic_as_topic_text_pairs():
    """Builds the job graph only; reading from a real Kafka is tested end to end with S4."""
    import json

    from pyflink.common import Types

    from ace.stage import flink_env

    env = flink_env()
    kafka.add_connector(env)
    stream = kafka.read_topics(env, ["jobs.clean", "raw.profiles"], "localhost:9092", "test", bounded=True)
    assert stream.get_type() == Types.TUPLE([Types.STRING(), Types.STRING()])
    stream.print()
    plan = json.loads(env.get_execution_plan())
    sources = sorted(node["contents"] for node in plan["nodes"] if node["type"].startswith("Source"))
    assert sources == ["Source: Kafka jobs.clean", "Source: Kafka raw.profiles"]


@pytest.mark.flink
def test_write_topics_adds_one_kafka_sink_for_all_output_topics():
    """Builds the job graph only; writing to a real Kafka is tested end to end with S4."""
    import json

    from pyflink.common import Types

    from ace.stage import flink_env

    env = flink_env()
    kafka.add_connector(env)
    outputs = env.from_collection(
        [("agg.demand_daily", "DE", "{}"), ("errors.invalid", "skills.demand", "{}")],
        type_info=Types.TUPLE([Types.STRING(), Types.STRING(), Types.STRING()]))
    kafka.write_topics(outputs, "localhost:9092")
    plan = json.loads(env.get_execution_plan())
    nodes = [node["contents"] for node in plan["nodes"]]
    assert nodes.count("Kafka sink: Writer") == 1


class Counts(WindowedStage):
    """A tiny windowed stage for building a job graph; never run here."""

    name = "test-counts"
    input = "skills.demand"
    output = "agg.demand_daily"
    time_field = "posted_date"


def test_load_stage_imports_module_and_class():
    stage = kafka.load_stage(f"{__name__}:Counts")
    assert isinstance(stage, Counts)
    for bad in ("no_colon", ":Counts", f"{__name__}:"):
        with pytest.raises(ValueError, match="module:Class"):
            kafka.load_stage(bad)


REPO = Path(__file__).resolve().parents[3]
LOAD_S4 = ("from ace.kafka import load_stage; "
           "print(type(load_stage('s4_demand_frequency.stage:DemandFrequency')).__name__)")


@pytest.mark.parametrize("folder", [".", "streaming"])
def test_load_stage_finds_a_real_stage_from_the_root_and_from_streaming(folder):
    """The README command works from both folders. A fresh Python, so pytest's own paths don't help."""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONPATH"] = str(REPO / "shared" / "python")   # ace itself, as pip install -e gives it
    result = subprocess.run([sys.executable, "-c", LOAD_S4], cwd=REPO / folder, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "DemandFrequency"


@pytest.mark.flink
def test_build_job_reads_the_input_topic_and_writes_to_kafka():
    import json

    env = kafka.build_job(Counts(), "localhost:9092", bounded=True)
    nodes = [node["contents"] for node in json.loads(env.get_execution_plan())["nodes"]]
    assert "Source: Kafka skills.demand" in nodes
    assert nodes.count("Kafka sink: Writer") == 1
