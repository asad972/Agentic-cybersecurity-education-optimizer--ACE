"""CLI entry point for publishing canonical ISC2 workforce records."""

import argparse
from collections.abc import Mapping
from typing import Any

from m5_runtime import parse_integer, publish_file, read_records
from workforce import normalize_workforce_records


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Canonical JSON, JSONL, or CSV file")
    args = parser.parse_args()
    messages = normalize_input(read_records(args.input))
    count = publish_file("raw.workforce", "m5-workforce", messages)
    print(f"Published {count} workforce records to raw.workforce")


if __name__ == "__main__":
    main()
