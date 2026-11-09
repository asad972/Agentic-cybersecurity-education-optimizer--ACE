"""CLI entry point for publishing canonical ISC2 workforce records."""

from collections.abc import Mapping
from typing import Any

from m5_runtime import (
    parse_integer,
    run_agent,
)
from workforce import normalize_workforce_record

TOPIC = "raw.workforce"
PRODUCER = "m5-workforce"


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
    run_agent(
        topic=TOPIC,
        producer_name=PRODUCER,
        normalizer_factory=lambda: normalize_record,
        input_help="Canonical JSON, JSONL, or CSV file",
        output_label="workforce",
        description=__doc__,
    )


if __name__ == "__main__":
    main()
