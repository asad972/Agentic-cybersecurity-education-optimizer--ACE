"""Bounded fixture runner; Kafka source/sink wiring belongs to deployment."""

import argparse
import json
from pathlib import Path

from ace import contracts

from .job import run_s7_flink_local, run_s7_local


def read_records(path: Path):
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except ValueError:
                # Let shared validation report malformed JSON in errors.invalid.
                yield line.rstrip("\r\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="S7 hourly/daily threat frequency fixture runner")
    parser.add_argument("--input", type=Path, required=True, help="Finite JSONL input")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--engine", choices=("local", "flink"), default="local")
    args = parser.parse_args(argv)
    records = list(read_records(args.input))
    if args.engine == "local":
        result = run_s7_local(records)
    else:
        result = run_s7_flink_local(records)
    outputs = result.outputs
    print(json.dumps({"late_hourly": len(result.hourly.late), "late_daily": len(result.daily.late)}))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for topic, filename in (
        (contracts.topic("agg.threats_hourly").name, "hourly.jsonl"),
        (contracts.topic("agg.threats_daily").name, "daily.jsonl"),
        (contracts.INVALID_TOPIC, "invalid.jsonl"),
    ):
        rows = [out.value for out in outputs if out.topic == topic]
        rows.sort(key=lambda row: (row.get("day", ""), row.get("hour", 0), row.get("country", "")))
        with (args.output_dir / filename).open("w", encoding="utf-8") as target:
            for row in rows:
                target.write(json.dumps(row, sort_keys=True) + "\n")
        print(f"{filename}: {len(rows)} rows")


if __name__ == "__main__":
    main()
