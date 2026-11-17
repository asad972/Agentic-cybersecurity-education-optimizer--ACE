"""CLI entry point for publishing canonical CYBERHEAD programme records."""

import argparse
from collections.abc import Mapping
from typing import Any

from education import normalize_programme, normalize_programmes
from m5_runtime import (
    KafkaUnavailable,
    parse_json_list,
    publish_normalized_file,
    read_records,
)


def normalize_input(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize canonical programme rows, including JSON-list CSV cells."""
    parsed = [
        {
            **record,
            "ecsf_roles": (
                []
                if record.get("ecsf_roles") is None
                else parse_json_list(record["ecsf_roles"], "ecsf_roles")
            ),
        }
        for record in records
    ]
    return normalize_programmes(parsed)


def normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one source row, decoding CSV list values when needed."""
    parsed = {
        **record,
        "ecsf_roles": (
            []
            if record.get("ecsf_roles") is None
            else parse_json_list(record["ecsf_roles"], "ecsf_roles")
        ),
    }
    return normalize_programme(parsed)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Canonical JSON, JSONL, or CSV file")
    args = parser.parse_args()
    records = read_records(args.input)
    try:
        count = publish_normalized_file(
            "raw.programmes", "m5-education", records, normalize_record
        )
    except KafkaUnavailable as error:
        parser.exit(2, f"{parser.prog}: error: {error}\n")
    print(f"Processed and published {count} programme rows")


if __name__ == "__main__":
    main()
