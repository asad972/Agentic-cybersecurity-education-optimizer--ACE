"""CLI entry point for publishing canonical ISC2 workforce records."""

import argparse
from collections.abc import Mapping
from typing import Any

from m5_runtime import (
    KafkaUnavailable,
    parse_integer,
    publish_normalized_file,
    read_records,
)
from workforce import normalize_workforce_record, normalize_workforce_records


def normalize_input(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize canonical workforce rows, including integer CSV cells."""
    parsed = [
        {
            **record,
            **{
                field: parse_integer(record.get(field), field)
                for field in ("year", "workforce_size", "workforce_gap")
                if field in record
            },
        }
        for record in records
    ]
    return normalize_workforce_records(parsed)


def normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one source row, converting CSV integer fields."""
    parsed = {
        **record,
        **{
            field: parse_integer(record.get(field), field)
            for field in ("year", "workforce_size", "workforce_gap")
            if field in record
        },
    }
    return normalize_workforce_record(parsed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Canonical JSON, JSONL, or CSV file")
    args = parser.parse_args()
    records = read_records(args.input)
    try:
        count = publish_normalized_file(
            "raw.workforce", "m5-workforce", records, normalize_record
        )
    except KafkaUnavailable as error:
        parser.exit(2, f"{parser.prog}: error: {error}\n")
    print(f"Processed and published {count} workforce rows")


if __name__ == "__main__":
    main()
