"""The command-line runner on the fixture file the container runs by default."""

import json
from pathlib import Path

from s3_top10.__main__ import main

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "job_ads.jsonl"


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_fixture_run_writes_top10s_and_invalid(tmp_path):
    main(["--input", str(FIXTURE), "--output-dir", str(tmp_path)])

    tops = {(m["country"], m["day"], m["kind"]): [(i["item"], i["count"]) for i in m["items"]]
            for m in lines(tmp_path / "top10_demand.jsonl")}
    # Four (Country, day) windows, each with a Skill and a Certification Top 10.
    assert len(tops) == 8
    assert tops["DE", "2026-10-01", "skill"][0] == ("SIEM", 2)
    assert tops["DE", "2026-10-03", "skill"][0] == ("GDPR", 3)

    invalid = lines(tmp_path / "invalid.jsonl")
    assert len(invalid) == 1 and "Germany" in invalid[0]["payload"]
