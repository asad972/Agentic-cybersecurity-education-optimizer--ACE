"""CLI entry point for publishing canonical ISC2 workforce records."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from m5_runtime import (
    parse_integer,
    parse_number,
    run_agent,
)
from workforce import normalize_workforce_record

TOPIC = "raw.workforce"
PRODUCER = "m5-workforce"
DEFAULT_INPUT = (
    Path(__file__).resolve().parents[1]
    / "sources"
    / "isc2_2024_global.json"
)


def normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one source row, converting CSV integer fields."""
    parsed = {
        **record,
        **{
            field: parse_integer(record.get(field), field)
            for field in ("year", "workforce_size", "workforce_gap")
            if field in record
        },
        **{
            field: parse_number(record.get(field), field)
            for field in (
                "workforce_growth_percent",
                "workforce_gap_growth_percent",
            )
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
        input_default=DEFAULT_INPUT,
    )


if __name__ == "__main__":
    main()
