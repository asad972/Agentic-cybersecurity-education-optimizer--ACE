"""Normalize raw profile and Stack Overflow Developer Survey rows into ace/raw_profile."""

from __future__ import annotations

from datetime import datetime
import re
import uuid
from typing import Any

# European Skills Scope from docs/ARCHITECTURE.md: EU27, EEA (IS, LI, NO), UK (GB), and CH.
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
    "UNITED KINGDOM OF GREAT BRITAIN AND NORTHERN IRELAND": "GB",
    "GREAT BRITAIN": "GB",
    "GERMANY": "DE",
    "DEUTSCHLAND": "DE",
    "AUSTRIA": "AT",
    "ÖSTERREICH": "AT",
    "SWITZERLAND": "CH",
    "SCHWEIZ": "CH",
    "SUISSE": "CH",
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
    "BELGIUM": "BE",
    "SWEDEN": "SE",
    "FINLAND": "FI",
    "DENMARK": "DK",
    "NORWAY": "NO",
    "ICELAND": "IS",
    "LIECHTENSTEIN": "LI",
    "IRELAND": "IE",
    "REPUBLIC OF IRELAND": "IE",
    "PORTUGAL": "PT",
    "ROMANIA": "RO",
    "GREECE": "GR",
    "BULGARIA": "BG",
    "CROATIA": "HR",
    "CYPRUS": "CY",
    "ESTONIA": "EE",
    "LATVIA": "LV",
    "LITHUANIA": "LT",
    "LUXEMBOURG": "LU",
    "MALTA": "MT",
    "SLOVAKIA": "SK",
    "SLOVENIA": "SI",
}

_SECURITY_ROLE_KEYWORDS: tuple[str, ...] = (
    "security",
    "cyber",
    "penetration",
    "pentest",
    "soc ",
    "incident",
    "forensic",
    "threat",
    "devsecops",
    "cryptograph",
    "compliance",
    "auditor",
    "risk",
    "ciso",
)

_SECURITY_SKILL_KEYWORDS: tuple[str, ...] = (
    "linux",
    "docker",
    "kubernetes",
    "terraform",
    "aws",
    "azure",
    "python",
    "bash",
    "powershell",
    "wireshark",
    "splunk",
    "burp",
    "metasploit",
    "kali",
    "nmap",
    "siem",
    "soar",
    "owasp",
    "iso 27001",
    "gdpr",
    "nist",
    "firewall",
    "zero trust",
    "yara",
    "sigma",
    "misp",
    "osint",
    "cryptography",
    "pki",
)

_SO_SKILL_COLUMNS: tuple[str, ...] = (
    "LanguageHaveWorkedWith",
    "DatabaseHaveWorkedWith",
    "PlatformHaveWorkedWith",
    "WebframeHaveWorkedWith",
    "MiscTechHaveWorkedWith",
    "ToolsTechHaveWorkedWith",
    "OpSysProfessional use",
    "AISearchDevHaveWorkedWith",
)

_UUID_NAMESPACE = uuid.UUID("6ba7b812-9dad-11d1-80b4-00c04fd430c8")


def normalize_country(raw_country: Any) -> str | None:
    """Return an ISO 3166-1 alpha-2 country code in the European Skills Scope, or None."""
    if raw_country is None:
        return None
    if isinstance(raw_country, dict):
        raw_country = raw_country.get("code") or raw_country.get("country") or raw_country.get("name")
    text = " ".join(str(raw_country).strip().upper().split())
    if not text or text == "NA":
        return None
    code = _COUNTRY_ALIASES.get(text, text)
    if len(code) == 2 and code.isalpha() and code in SKILLS_SCOPE_COUNTRIES:
        return code
    return None


def _split_semicolon(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        items = [str(x).strip() for x in raw]
    else:
        text = str(raw).strip()
        if not text or text.upper() == "NA":
            return []
        items = [part.strip() for part in text.split(";")]
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item.upper() != "NA":
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                result.append(item)
    return result


def _first_non_empty(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        val = row.get(key)
        if val is not None:
            text = " ".join(str(val).strip().split())
            if text and text.upper() != "NA":
                return text
    return ""


def _is_stackoverflow_row(row: dict[str, Any]) -> bool:
    return any(
        key in row
        for key in (
            "ResponseId",
            "DevType",
            "LanguageHaveWorkedWith",
            "ToolsTechHaveWorkedWith",
            "PlatformHaveWorkedWith",
            "YearsCodePro",
        )
    )


def _is_security_relevant(roles: list[str], skills: list[str]) -> bool:
    roles_lower = " | ".join(roles).lower()
    if any(kw in roles_lower for kw in _SECURITY_ROLE_KEYWORDS):
        return True
    skills_lower = " | ".join(skills).lower()
    return any(kw in skills_lower for kw in _SECURITY_SKILL_KEYWORDS)


def _resolve_event_id(
    row: dict[str, Any],
    source: str,
    country: str,
    profile_date: str,
    headline: str,
    text: str,
) -> str:
    raw_id = row.get("event_id")
    if raw_id is not None and str(raw_id).strip():
        try:
            return str(uuid.UUID(str(raw_id).strip()))
        except ValueError:
            pass

    natural_key = _first_non_empty(row, ("ResponseId", "response_id", "profile_id", "id")) or (
        f"{source}|{country}|{profile_date}|{headline}|{text[:160]}"
    )
    return str(uuid.uuid5(_UUID_NAMESPACE, f"ace-profile:{source}:{natural_key}"))


def normalize_profile_row(
    row: dict[str, Any],
    *,
    default_source: str = "stackoverflow",
    default_country: str | None = None,
    default_profile_date: str | None = None,
    enforce_europe_scope: bool = True,
    security_only: bool = True,
) -> dict[str, Any] | None:
    """Map a raw profile or Stack Overflow survey row into ``ace/raw_profile``."""
    raw_country = row.get("country") if row.get("country") is not None else row.get("Country")
    if raw_country is not None and str(raw_country).strip() != "" and str(raw_country).strip().upper() != "NA":
        country = normalize_country(raw_country)
        if country is None:
            if enforce_europe_scope:
                return None
            raise ValueError(f"unsupported country {raw_country!r}")
    else:
        if default_country is not None:
            country = normalize_country(default_country)
            if country is None:
                raise ValueError(f"unsupported default_country {default_country!r}")
        elif enforce_europe_scope and ("Country" in row or "country" in row):
            return None
        else:
            raise ValueError("missing required field 'country'")

    source = _first_non_empty(row, ("source", "provider")) or default_source

    raw_date = _first_non_empty(row, ("profile_date", "survey_date", "date", "created_at"))
    if not raw_date and default_profile_date:
        raw_date = default_profile_date.strip()
    if not raw_date:
        raise ValueError("missing required field 'profile_date'")

    profile_date = raw_date[:10]
    try:
        datetime.strptime(profile_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"invalid profile_date {raw_date!r} (expected YYYY-MM-DD)") from exc

    if _is_stackoverflow_row(row):
        roles = _split_semicolon(row.get("DevType"))
        skills: list[str] = []
        seen_skills: set[str] = set()
        for col in _SO_SKILL_COLUMNS:
            for item in _split_semicolon(row.get(col)):
                k = item.casefold()
                if k not in seen_skills:
                    seen_skills.add(k)
                    skills.append(item)

        if security_only and not _is_security_relevant(roles, skills):
            return None

        years = _first_non_empty(row, ("YearsCodePro", "WorkExp", "YearsCode"))
        headline = _first_non_empty(row, ("headline", "title"))
        if not headline:
            headline = ", ".join(roles[:3]) if roles else "Cybersecurity & Software Practitioner"
        headline = headline[:200]

        parts: list[str] = []
        if roles:
            parts.append(f"Roles: {', '.join(roles)}.")
        if years:
            parts.append(f"Professional experience: {years} years.")
        if skills:
            parts.append(f"Core skills and tools: {', '.join(skills)}.")
        text = " ".join(parts).strip()
        if not text:
            raise ValueError("missing required field 'text'")
    else:
        headline = _first_non_empty(row, ("headline", "role", "title"))
        text = _first_non_empty(row, ("text", "bio", "summary", "description"))
        if not text:
            raw_skills = row.get("skills")
            skill_list = (
                _split_semicolon(raw_skills)
                if isinstance(raw_skills, list)
                else ([s.strip() for s in str(raw_skills).split(",") if s.strip()] if raw_skills else [])
            )
            years = _first_non_empty(row, ("years_experience", "YearsCodePro"))
            parts = []
            if headline:
                parts.append(f"Role: {headline}.")
            if years:
                parts.append(f"Experience: {years} years.")
            if skill_list:
                parts.append(f"Core skills and tools: {', '.join(skill_list)}.")
            text = " ".join(parts).strip()
        if not text:
            raise ValueError("missing required field 'text'")

    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[redacted]", text)
    event_id = _resolve_event_id(row, source, country, profile_date, headline, text)

    message: dict[str, Any] = {
        "event_id": event_id,
        "source": source,
        "country": country,
        "profile_date": profile_date,
        "text": text,
    }
    if headline:
        message["headline"] = headline
    return message
