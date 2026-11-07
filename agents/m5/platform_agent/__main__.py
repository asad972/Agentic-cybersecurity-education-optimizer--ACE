"""CLI entry point for publishing reviewed platform evidence."""

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from m5_runtime import publish_file, read_records
from platform_analysis import normalize_platform_evidence


def normalize_input(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize cited evidence, decoding boolean CSV cells strictly."""
    parsed = []
    for record in records:
        row = dict(record)
        supported = row.get("supported")
        if isinstance(supported, str):
            decision = supported.strip().lower()
            if decision not in {"true", "false"}:
                raise ValueError("supported in CSV must be true or false")
            row["supported"] = decision == "true"
        parsed.append(row)
    return normalize_platform_evidence(parsed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default=str(Path(__file__).resolve().parents[1] / "platform_evidence.json"),
        help="Reviewed JSON, JSONL, or CSV evidence file",
    )
    args = parser.parse_args()
    messages = normalize_input(read_records(args.input))
    count = publish_file("analysis.platforms", "m5-platform-analysis", messages)
    print(f"Published {count} platform evidence records to analysis.platforms")


if __name__ == "__main__":
    main()
