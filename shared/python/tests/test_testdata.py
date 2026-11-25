"""The fake test data in shared/testdata passes the same check every publisher runs."""

import importlib.util
import json
from pathlib import Path

import pytest

from ace import contracts

REPO_ROOT = Path(__file__).resolve().parents[3]
TESTDATA = REPO_ROOT / "shared" / "testdata"

# Each test-data file and the topic its records are published to.
TOPIC_OF = {
    "jobs_sample.jsonl": "raw.jobs",
    "profiles_sample.jsonl": "raw.profiles",
}


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_testdata", REPO_ROOT / "generate_testdata.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_testdata_file_has_a_known_topic():
    files = sorted(path.name for path in TESTDATA.iterdir() if path.is_file())
    assert files == sorted(TOPIC_OF), "add each file in shared/testdata to TOPIC_OF"
    for topic in TOPIC_OF.values():
        contracts.topic(topic)  # raises for a topic that is not in topics.csv


def failures(file_name):
    """'line N: reason' for every record in the file that fails its topic schema."""
    topic = TOPIC_OF[file_name]
    lines = (TESTDATA / file_name).read_text(encoding="utf-8").splitlines()
    found = []
    for number, line in enumerate(lines, start=1):
        if line.strip():
            reason = contracts.check(topic, json.loads(line))
            if reason is not None:
                found.append(f"line {number}: {reason}")
    return found


@pytest.mark.parametrize("file_name", sorted(TOPIC_OF))
def test_every_record_passes_its_topic_schema(file_name):
    lines = [
        line
        for line in (TESTDATA / file_name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 200
    found = failures(file_name)
    assert not found, f"{len(found)} records in {file_name} fail: " + "; ".join(found[:3])


def test_generator_is_deterministic_and_matches_committed_testdata():
    gen = _load_generator()
    assert gen.generate_jobs() == gen.generate_jobs(seed=gen.DEFAULT_SEED)
    assert gen.generate_profiles() == gen.generate_profiles(seed=gen.DEFAULT_SEED)

    committed_jobs = [
        json.loads(line)
        for line in (TESTDATA / "jobs_sample.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    committed_profiles = [
        json.loads(line)
        for line in (TESTDATA / "profiles_sample.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert gen.generate_jobs() == committed_jobs
    assert gen.generate_profiles() == committed_profiles
