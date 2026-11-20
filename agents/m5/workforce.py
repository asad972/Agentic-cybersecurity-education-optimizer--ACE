"""Normalize extracted workforce-report figures into ACE messages."""

import math
from collections.abc import Iterable, Mapping
from typing import Any


def normalize_workforce_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one scoped workforce figure to the ACE message contract."""
    geography_level = record.get(
        "geography_level", "country" if "country" in record else None
    )
    if not isinstance(geography_level, str):
        raise ValueError("geography_level must be country, region, or global")
    geography_level = geography_level.strip().lower()
    if geography_level not in {"country", "region", "global"}:
        raise ValueError("geography_level must be country, region, or global")

    geography = _text(
        record.get("geography", record.get("country")), "geography"
    )
    if geography_level == "country":
        geography = geography.upper()
        if (
            len(geography) != 2
            or not geography.isascii()
            or not geography.isalpha()
            or geography == "EU"
        ):
            raise ValueError(
                "country geography must be a two-letter ISO country code; "
                "use region for EU or other regions"
            )
    elif geography_level == "global":
        if geography.upper() != "GLOBAL":
            raise ValueError("global geography must be GLOBAL")
        geography = "GLOBAL"

    year = record.get("year")
    if isinstance(year, bool) or not isinstance(year, int) or year < 1900:
        raise ValueError("year must be an integer of 1900 or later")
    source = _text(record.get("source"), "source")

    message: dict[str, Any] = {
        "geography_level": geography_level,
        "geography": geography,
        "year": year,
        "source": source,
    }
    source_reference = record.get("source_reference")
    if source_reference is not None:
        message["source_reference"] = _text(
            source_reference, "source_reference"
        )

    for field in ("workforce_size", "workforce_gap"):
        value = record.get(field)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
            message[field] = value

    for field in (
        "workforce_growth_percent",
        "workforce_gap_growth_percent",
    ):
        value = record.get(field)
        if value is not None:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or (isinstance(value, float) and not math.isfinite(value))
            ):
                raise ValueError(f"{field} must be a finite number")
            message[field] = value

    return message


def normalize_workforce_records(
    records: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize a sequence of extracted figures without inventing missing data."""
    return [normalize_workforce_record(record) for record in records]


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()
