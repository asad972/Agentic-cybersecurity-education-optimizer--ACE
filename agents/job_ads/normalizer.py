"""Normalize raw job advertisement rows into the ace/raw_job contract."""

from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any

# Skills Scope from docs/ARCHITECTURE.md: EU27, EEA (IS, LI, NO), UK (GB), and CH.
SKILLS_SCOPE_COUNTRIES: frozenset[str] = frozenset(
    {
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
        "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
        "PL", "PT", "RO", "SK", "SI", "ES", "SE",
        "IS", "LI", "NO",
        "GB", "CH",
    }
)

_COUNTRY_ALIASES: dict[str, str] = {
    "UK": "GB",
    "EL": "GR",
    "UNITED KINGDOM": "GB",
    "GREAT BRITAIN": "GB",
    "GERMANY": "DE",
    "DEUTSCHLAND": "DE",
    "FRANCE": "FR",
    "SPAIN": "ES",
    "ESPAÑA": "ES",
    "ITALY": "IT",
    "ITALIA": "IT",
    "NETHERLANDS": "NL",
    "THE NETHERLANDS": "NL",
    "POLAND": "PL",
    "HUNGARY": "HU",
    "CZECHIA": "CZ",
    "CZECH REPUBLIC": "CZ",
    "AUSTRIA": "AT",
    "BELGIUM": "BE",
    "SWEDEN": "SE",
    "FINLAND": "FI",
    "DENMARK": "DK",
    "NORWAY": "NO",
    "SWITZERLAND": "CH",
    "IRELAND": "IE",
    "PORTUGAL": "PT",
    "ROMANIA": "RO",
    "GREECE": "GR",
}

_UUID_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")


def normalize_country(raw_country: Any) -> str | None:
    """Return an ISO 3166-1 alpha-2 country code in the European Skills Scope, or None."""
    if raw_country is None:
        return None
    if isinstance(raw_country, dict):
        raw_country = raw_country.get("code") or raw_country.get("country") or raw_country.get("display_name")
    text = str(raw_country).strip().upper()
    if not text:
        return None
    code = _COUNTRY_ALIASES.get(text, text)
    if len(code) == 2 and code.isalpha() and code in SKILLS_SCOPE_COUNTRIES:
        return code
    return None


def _country_from_nuts(raw_nuts: Any) -> str | None:
    """Extract a 2-letter European Skills Scope country prefix from a NUTS code (e.g. 'DEF' -> 'DE')."""
    if raw_nuts is None:
        return None
    text = str(raw_nuts).strip().upper()
    if len(text) >= 2 and text[:2].isalpha():
        return normalize_country(text[:2])
    return None


def _first_non_empty(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        val = row.get(key)
        if isinstance(val, dict):
            val = val.get("display_name") or val.get("name") or val.get("label")
        if val is not None:
            text = " ".join(str(val).strip().split())
            if text:
                return text
    return ""


def _description_from_esco(row: dict[str, Any]) -> str:
    """Build a fallback description from structured ESCO occupation/skills fields (e.g. Hugging Face dataset)."""
    parts: list[str] = []

    raw_occ = row.get("esco_occupation")
    occ_label = ""
    if isinstance(raw_occ, dict):
        occ_label = str(raw_occ.get("label") or raw_occ.get("name") or "").strip()
    elif isinstance(raw_occ, str):
        occ_label = raw_occ.strip()
    if occ_label:
        parts.append(f"Occupation: {occ_label}.")

    raw_skills = row.get("esco_skills")
    skill_labels: list[str] = []
    if isinstance(raw_skills, list):
        for item in raw_skills:
            if isinstance(item, dict):
                lbl = str(item.get("label") or item.get("name") or "").strip()
            elif isinstance(item, str):
                lbl = item.strip()
            else:
                lbl = ""
            if lbl:
                skill_labels.append(lbl)
    if skill_labels:
        parts.append("Required skills and technologies: " + ", ".join(skill_labels) + ".")

    return " ".join(parts).strip()


def _resolve_event_id(
    row: dict[str, Any],
    source: str,
    country: str,
    posted_date: str,
    title: str,
    company: str,
    url: str,
    description: str,
) -> str:
    raw_id = row.get("event_id")
    if raw_id is not None and str(raw_id).strip():
        try:
            return str(uuid.UUID(str(raw_id).strip()))
        except ValueError:
            pass

    natural_key = _first_non_empty(row, ("id", "job_id", "ad_id")) or (
        f"{source}|{country}|{posted_date}|{title}|{company}|{url}|{description[:120]}"
    )
    return str(uuid.uuid5(_UUID_NAMESPACE, f"ace-job:{source}:{natural_key}"))


def normalize_job_row(
    row: dict[str, Any],
    *,
    default_source: str = "job_ads_dataset",
    default_country: str | None = None,
    enforce_europe_scope: bool = True,
) -> dict[str, Any] | None:
    """Map one raw dataset row into a ``raw.jobs`` message dictionary.

    Returns ``None`` if the row belongs to a country outside the European Skills
    Scope (when ``enforce_europe_scope=True``). Raises ``ValueError`` if required
    fields (``country``, ``posted_date``, ``job_title``, ``description``) are missing
    or malformed.
    """
    raw_country = row.get("country")
    if raw_country is None and isinstance(row.get("location"), dict):
        raw_country = row["location"].get("country") or row["location"].get("area")

    if raw_country is not None and str(raw_country).strip() != "":
        country = normalize_country(raw_country)
        if country is None:
            if enforce_europe_scope:
                return None
            raise ValueError(f"unsupported country {raw_country!r}")
    else:
        country = _country_from_nuts(row.get("nuts_code"))
        if country is None and default_country is not None:
            country = normalize_country(default_country)
            if country is None:
                raise ValueError(f"unsupported default_country {default_country!r}")
        if country is None:
            raise ValueError("missing required field 'country'")

    source = _first_non_empty(row, ("source", "provider", "portal")) or default_source

    raw_date = _first_non_empty(row, ("posted_date", "created", "date", "published_at", "publication_date"))
    if not raw_date:
        raise ValueError("missing required field 'posted_date'")
    posted_date = raw_date[:10]
    try:
        datetime.strptime(posted_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"invalid posted_date {raw_date!r} (expected YYYY-MM-DD)") from exc

    job_title = _first_non_empty(row, ("job_title", "title", "position", "role"))
    if not job_title:
        raise ValueError("missing required field 'job_title'")

    description = _first_non_empty(
        row,
        ("description", "job_description", "description_derived", "body", "summary", "text"),
    )
    if not description:
        description = _description_from_esco(row)
    if not description:
        raise ValueError("missing required field 'description'")

    url = _first_non_empty(row, ("url", "source_url", "redirect_url", "job_url", "link"))
    company = _first_non_empty(row, ("company", "company_name", "employer", "organization"))
    event_id = _resolve_event_id(row, source, country, posted_date, job_title, company, url, description)

    message: dict[str, Any] = {
        "event_id": event_id,
        "source": source,
        "country": country,
        "posted_date": posted_date,
        "job_title": job_title,
        "description": description,
    }
    if company:
        message["company"] = company
    if url:
        message["url"] = url
    return message