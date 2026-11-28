"""Run S1 near-duplicate job filter over a JSONL file of raw.jobs messages."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ace import contracts
from ace.stage import run_flink_local, run_local

from s1_dedup.stage import OUTPUT_TOPIC, S1DedupStage

OUTPUT_FILES = (
    (OUTPUT_TOPIC, "jobs_clean.jsonl"),
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
                yield line.rstrip("\r\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="S1 near-duplicate filter on raw.jobs JSONL")
    parser.add_argument("--input", type=Path, required=True, help="JSONL file of raw.jobs messages")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--engine", choices=("local", "flink"), default="local")
    args = parser.parse_args(argv)

    records = list(read_records(args.input))
    if args.engine == "local":
        outputs = run_local(S1DedupStage(), records).outputs
    else:
        outputs = run_flink_local(S1DedupStage(), records)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for topic, filename in OUTPUT_FILES:
        rows = [out.value for out in outputs if out.topic == topic]
        with (args.output_dir / filename).open("w", encoding="utf-8") as target:
            for row in rows:
                target.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        print(f"{filename}: {len(rows)} rows")


if __name__ == "__main__":
    main()