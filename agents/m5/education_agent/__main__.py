"""CLI entry point for publishing canonical CYBERHEAD programme records."""

import argparse
from collections.abc import Mapping
from typing import Any

from education import normalize_programmes
from m5_runtime import parse_json_list, publish_file, read_records


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Canonical JSON, JSONL, or CSV file")
    args = parser.parse_args()
    messages = normalize_input(read_records(args.input))
    count = publish_file("raw.programmes", "m5-education", messages)
    print(f"Published {count} programme records to raw.programmes")


if __name__ == "__main__":
    main()
