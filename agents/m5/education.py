"""Normalize education programme records into ACE raw.programmes messages."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any


_REQUIRED_FIELDS = ("programme_id", "country", "name", "institution")
_LEVELS = {"bachelor", "master", "phd", "other"}


def normalize_programme(record: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one already-extracted CYBERHEAD record to the ACE contract.

    CYBERHEAD source layouts can change; source-specific extraction should
    produce these canonical fields before calling this function.
    """
    missing = [field for field in _REQUIRED_FIELDS if field not in record]
    if missing:
        raise ValueError(f"programme record is missing fields: {', '.join(missing)}")

    programme_id = _required_text(record["programme_id"], "programme_id")
    country = _country(record["country"])
    name = _required_text(record["name"], "name")
    institution = _required_text(record["institution"], "institution")
    roles = _text_list(record.get("ecsf_roles", []), "ecsf_roles")
    level = record.get("level")
    if level is not None:
        if not isinstance(level, str):
            raise ValueError("level must be text")
        level = level.strip().lower()
        if level not in _LEVELS:
            raise ValueError(f"unsupported programme level: {level}")

    message: dict[str, Any] = {
        "programme_id": programme_id,
        "country": country,
        "name": name,
        "institution": institution,
        "ecsf_roles": roles,
    }
    if level:
        message["level"] = level
    if record.get("url") is not None:
        message["url"] = _required_text(record["url"], "url")
    return message


def normalize_programmes(
    records: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize a sequence of source records, failing on invalid input."""
    return [normalize_programme(record) for record in records]


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()


def _country(value: Any) -> str:
    country = _required_text(value, "country")
    if len(country) != 2 or not country.isalpha():
        raise ValueError("country must be a two-letter ISO country code")
    return country.upper()


def _text_list(value: Any, field: str) -> list[str]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be a list of text values")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} must contain only non-empty text values")
        result.append(item.strip())
    return result
