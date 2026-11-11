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
    """Normalize a programme record with already-reviewed ECSF role labels."""
    raw_roles = record.get("ecsf_roles")
    roles = [] if raw_roles is None else parse_json_list(raw_roles, "ecsf_roles")
    return normalize_programme({**record, "ecsf_roles": roles})


def main() -> None:
    run_agent(
        topic=TOPIC,
        producer_name=PRODUCER,
        normalizer_factory=lambda: normalize_record,
        input_help="CYBERHEAD JSON, JSONL, or CSV export",
        output_label="programme",
        description=__doc__,
    )


if __name__ == "__main__":
    main()
