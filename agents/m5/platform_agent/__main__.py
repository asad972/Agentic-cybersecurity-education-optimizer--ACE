"""CLI entry point for publishing reviewed platform evidence."""

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from m5_runtime import KafkaUnavailable, publish_normalized_file, read_records
from platform_analysis import normalize_feature_evidence, normalize_platform_evidence


def normalize_record(
    record: Mapping[str, Any], analysed_at: datetime
) -> dict[str, Any]:
    """Normalize one evidence row, strictly decoding CSV boolean cells."""
    row = dict(record)
    supported = row.get("supported")
    if isinstance(supported, str):
        decision = supported.strip().lower()
        if decision not in {"true", "false"}:
            raise ValueError("supported in CSV must be true or false")
        row["supported"] = decision == "true"
    return normalize_feature_evidence(row, analysed_at=analysed_at)


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
    records = read_records(args.input)
    analysed_at = datetime.now(timezone.utc)
    try:
        count = publish_normalized_file(
            "analysis.platforms",
            "m5-platform-analysis",
            records,
            lambda record: normalize_record(record, analysed_at),
        )
    except KafkaUnavailable as error:
        parser.exit(2, f"{parser.prog}: error: {error}\n")
    print(f"Processed and published {count} platform evidence rows")


if __name__ == "__main__":
    main()
