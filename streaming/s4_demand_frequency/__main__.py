"""Run S4 over a file of skills.demand messages and write what it publishes.

    python -m s4_demand_frequency --input s4_demand_frequency/fixtures/job_ads.jsonl --output-dir out

Run from streaming/. The input is one JSON message per line. At the end of the
file every open day closes, as when a Flink job reaches the end of a bounded
input. Live Kafka source and sink wiring belongs to the deployment.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator

from ace import contracts
from ace.stage import run_flink_local, run_local
from s4_demand_frequency.stage import DemandFrequency

OUTPUT_FILES = (
    (DemandFrequency.output, "demand_daily.jsonl"),
    (contracts.INVALID_TOPIC, "invalid.jsonl"),
)


def read_records(path: Path) -> Iterator[Any]:
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except ValueError:
                # Shared validation sends malformed JSON to errors.invalid.
                yield line.rstrip("\r\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="S4 Demand frequency on a file of skills.demand messages")
    parser.add_argument("--input", type=Path, required=True, help="JSONL file of skills.demand messages")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--engine", choices=("local", "flink"), default="local")
    args = parser.parse_args(argv)

    records = list(read_records(args.input))
    if args.engine == "local":
        result = run_local(DemandFrequency(), records)
        outputs, late = result.outputs, len(result.late)
        print(f"late records: {late}")
    else:
        # The local Flink run does not return late records; Flink counts them
        # in the late_records_total metric instead.
        outputs = run_flink_local(DemandFrequency(), records)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for topic, filename in OUTPUT_FILES:
        rows = [out.value for out in outputs if out.topic == topic]
        rows.sort(key=lambda row: (row.get("day", ""), row.get("country", "")))
        with (args.output_dir / filename).open("w", encoding="utf-8") as target:
            for row in rows:
                target.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        print(f"{filename}: {len(rows)} rows")


if __name__ == "__main__":
    main()
