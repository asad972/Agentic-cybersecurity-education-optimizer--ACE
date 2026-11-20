"""Command-line entry point for the M3 experiment harness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .harness import SEED, build_results, canonical_json, render_table


def _load(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read experiment results: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit("experiment results must contain one JSON object")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    modes = result.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--revision",
        metavar="GIT_SHA",
        help="run the canonical experiments and label them with this revision",
    )
    modes.add_argument(
        "--check",
        type=Path,
        metavar="RESULTS_JSON",
        help="recompute and compare with checked-in canonical results",
    )
    modes.add_argument(
        "--table",
        type=Path,
        metavar="RESULTS_JSON",
        help="render the compact Markdown results table",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.revision is not None:
        sys.stdout.write(canonical_json(build_results(arguments.revision)))
        return 0

    expected = _load(arguments.check or arguments.table)
    if arguments.table is not None:
        print(render_table(expected))
        return 0

    if expected.get("schema_version") != 1:
        raise SystemExit("unsupported experiment-results schema_version")
    if expected.get("seed") != SEED:
        raise SystemExit(f"results seed must be the canonical value {SEED}")
    revision = expected.get("revision")
    if not isinstance(revision, str) or not revision.strip():
        raise SystemExit("results revision must be a non-empty string")
    actual = build_results(revision)
    if canonical_json(actual) != canonical_json(expected):
        raise SystemExit("recomputed experiment results do not match")
    print("experiment results match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
