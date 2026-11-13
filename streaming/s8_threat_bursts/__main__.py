"""Bounded fixture CLI; deployment attaches Kafka to ace.stage.build_flink."""

import argparse
import json
from pathlib import Path

from ace import contracts
from ace.stage import run_flink_local, run_local

from .fixtures import hourly_fixture
from .stage import BurstConfig, ThreatBurstStage


def read_records(path: Path):
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                try:
                    yield json.loads(line)
                except ValueError:
                    yield line.rstrip("\r\n")  # shared validation routes bad JSON


def main(argv=None):
    parser = argparse.ArgumentParser(description="S8 per-Country Half-Space Trees fixture runner")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="Hourly aggregate JSONL, ordered per Country")
    source.add_argument("--demo", action="store_true", help="Use synthetic HU/DE hourly aggregates")
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--engine", choices=("local", "flink"), default="local")
    parser.add_argument("--threshold", type=float, default=0.50)
    args = parser.parse_args(argv)
    try:
        stage = ThreatBurstStage(BurstConfig(threshold=args.threshold))
    except ValueError as error:
        parser.error(str(error))
    records = list(hourly_fixture() if args.demo else read_records(args.input))
    outputs = (run_flink_local(stage, records) if args.engine == "flink"
               else run_local(stage, records).outputs)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, topic in (("alerts", stage.output), ("invalid", contracts.INVALID_TOPIC)):
        rows = [out.value for out in outputs if out.topic == topic]
        (args.output_dir / f"{name}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8",
        )
        print(f"{name}.jsonl: {len(rows)} rows")


if __name__ == "__main__":
    main()
