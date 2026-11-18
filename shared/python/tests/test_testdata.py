"""The fake test data in shared/testdata passes the same check every publisher runs."""

import json
from pathlib import Path

import pytest

from ace import contracts

TESTDATA = Path(__file__).resolve().parents[2] / "testdata"

# Each test-data file and the topic its records are published to.
TOPIC_OF = {
    "jobs_sample.jsonl": "raw.jobs",
    "profiles_sample.jsonl": "raw.profiles",
}


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


@pytest.mark.parametrize("file_name", [
    "jobs_sample.jsonl",
    pytest.param("profiles_sample.jsonl", marks=pytest.mark.xfail(
        strict=True, reason="#71: profiles have no 'text' and extra fields; remove this mark once fixed")),
])
def test_every_record_passes_its_topic_schema(file_name):
    found = failures(file_name)
    assert not found, f"{len(found)} records in {file_name} fail: " + "; ".join(found[:3])
