"""Run S2 skill extraction over a JSONL file of jobs.clean or raw.profiles messages."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ace import contracts
from ace.stage import run_flink_local, run_local

from s2_skill_extraction.stage import (
    JOBS_CLEAN,
    SKILLS_DEMAND,
    SKILLS_SUPPLY,
    S2SkillExtractionStage,
)

OUTPUT_FILES = (
    (SKILLS_DEMAND, "skills_demand.jsonl"),
    (SKILLS_SUPPLY, "skills_supply.jsonl"),
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
    parser = argparse.ArgumentParser(description="S2 skill extraction on jobs.clean / raw.profiles JSONL")
    parser.add_argument("--input", type=Path, required=True, help="JSONL file of input records")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--topic", default=JOBS_CLEAN, help="Input topic name (default: jobs.clean)")
    parser.add_argument("--engine", choices=("local", "flink"), default="local")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM extractor and use taxonomy only")
    args = parser.parse_args(argv)

    records = list(read_records(args.input))
    stage = S2SkillExtractionStage(use_llm=False if args.no_llm else None)
    if args.engine == "local":
        outputs = run_local(stage, records, topic=args.topic).outputs
    else:
        outputs = run_flink_local(stage, records, topic=args.topic)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for topic, filename in OUTPUT_FILES:
        rows = [out.value for out in outputs if out.topic == topic]
        with (args.output_dir / filename).open("w", encoding="utf-8") as target:
            for row in rows:
                target.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        print(f"{filename}: {len(rows)} rows")


if __name__ == "__main__":
    main()