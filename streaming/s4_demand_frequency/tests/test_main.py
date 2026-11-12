"""The command-line runner on the fixture file the container runs by default."""

import json
from pathlib import Path

from s4_demand_frequency.__main__ import main

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "job_ads.jsonl"


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_fixture_run_writes_days_and_invalid(tmp_path):
    main(["--input", str(FIXTURE), "--output-dir", str(tmp_path)])

    days = {(m["country"], m["day"]): m for m in lines(tmp_path / "demand_daily.jsonl")}
    assert sorted(days) == [("DE", "2026-10-01"), ("DE", "2026-10-02"),
                            ("DE", "2026-10-03"), ("FR", "2026-10-01")]
    assert days["DE", "2026-10-01"]["job_ads"] == 2

    invalid = lines(tmp_path / "invalid.jsonl")
    assert len(invalid) == 1 and "Germany" in invalid[0]["payload"]
