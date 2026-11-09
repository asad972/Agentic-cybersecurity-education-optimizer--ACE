"""CLI entry point for publishing canonical CYBERHEAD programme records."""

from collections.abc import Mapping
from typing import Any

from education import normalize_programme
from m5_runtime import (
    parse_json_list,
    run_agent,
)

TOPIC = "raw.programmes"
PRODUCER = "m5-education"


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
    run_agent(
        topic=TOPIC,
        producer_name=PRODUCER,
        normalizer_factory=lambda: normalize_record,
        input_help="Canonical JSON, JSONL, or CSV file",
        output_label="programme",
        description=__doc__,
    )


if __name__ == "__main__":
    main()
