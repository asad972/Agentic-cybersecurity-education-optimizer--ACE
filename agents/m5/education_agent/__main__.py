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
_SAMPLE_COUNTRY_CODES = {
    "austria": "AT",
    "germany": "DE",
    "netherlands": "NL",
    "spain": "ES",
}


def normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a CYBERHEAD export row without inferring ECSF roles."""
    country = record.get("country")
    if isinstance(country, str):
        country_name = country.strip()
        country = (
            country_name.upper()
            if len(country_name) == 2
            else _SAMPLE_COUNTRY_CODES.get(country_name.casefold(), country_name)
        )

    raw_roles = record.get("ecsf_roles")
    roles = (
        []
        if raw_roles is None
        else parse_json_list(raw_roles, "ecsf_roles")
    )
    return normalize_programme(
        {
            "programme_id": record.get("programme_id"),
            "country": country,
            "name": record.get("name", record.get("title")),
            "institution": record.get("institution", record.get("provider")),
            "ecsf_roles": roles,
            "level": record.get("level"),
            "url": record.get("url"),
        }
    )


def main() -> None:
    run_agent(
        topic=TOPIC,
        producer_name=PRODUCER,
        normalizer_factory=lambda: normalize_record,
        input_help="CYBERHEAD JSON, JSONL, or CSV export",
        output_label="programme",
        description=__doc__,
        records_key="programmes",
    )


if __name__ == "__main__":
    main()
