"""Normalize extracted workforce-report figures into ACE messages."""

from collections.abc import Iterable, Mapping
from typing import Any


def normalize_workforce_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one country/year workforce figure to the ACE message contract."""
    country = _text(record.get("country"), "country").upper()
    if len(country) != 2 or not country.isalpha():
        raise ValueError("country must be a two-letter ISO country code")

    year = record.get("year")
    if isinstance(year, bool) or not isinstance(year, int) or year < 1900:
        raise ValueError("year must be an integer of 1900 or later")
    source = _text(record.get("source"), "source")

    message: dict[str, Any] = {
        "country": country,
        "year": year,
        "source": source,
    }
    for field in ("workforce_size", "workforce_gap"):
        value = record.get(field)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
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
