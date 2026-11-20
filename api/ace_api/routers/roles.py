"""M1 issue #14: ECSF Role Explorer endpoints, auto-discovered by the API."""

from __future__ import annotations

from datetime import date as Date, datetime, timezone
import logging
from typing import Any

from cassandra import DriverException
from fastapi import APIRouter, HTTPException, Query, Request

from ..map_data import COUNTRIES
from ..roles_data import load_role_catalog, resolve_role, taxonomy_related_roles

log = logging.getLogger(__name__)
router = APIRouter(tags=["M1 Role explorer"])


def _validate_day(day: Date) -> None:
    if not Date(2020, 1, 1) <= day <= datetime.now(timezone.utc).date():
        raise HTTPException(
            status_code=422,
            detail="Choose a date from 2020-01-01 through today (UTC)",
        )


def _validate_country(country: str) -> str:
    code = country.strip().upper()
    if code not in COUNTRIES:
        raise HTTPException(
            status_code=422,
            detail="Choose a European Skills Scope country (EU27, EEA, UK, or CH)",
        )
    return code


def _session_if_ready(request: Request):
    database = getattr(request.app.state, "database", None)
    if database is None or not database.ready():
        return None
    try:
        return database.session
    except RuntimeError:
        return None


def _read_optional(session, query: str, parameters: tuple[Any, ...]) -> list[Any]:
    if session is None:
        return []
    try:
        return list(session.execute(query, parameters))
    except DriverException as error:
        log.warning("Role Explorer Cassandra read failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail="Cassandra read is unavailable",
        ) from error


def _counts_by_kind(
    session,
    table: str,
    country: str,
    day: Date,
) -> dict[str, dict[str, int]]:
    rows = _read_optional(
        session,
        f"SELECT kind, item, count FROM {table} WHERE country = %s AND day = %s",
        (country, day),
    )
    grouped: dict[str, dict[str, int]] = {"role": {}, "skill": {}, "certification": {}}
    for row in rows:
        kind = getattr(row, "kind", None)
        item = getattr(row, "item", None)
        count = getattr(row, "count", None)
        if kind in grouped and item and count is not None:
            grouped[kind][item] = int(count)
    return grouped


@router.get("/roles")
def list_roles(
    request: Request,
    country: str | None = Query(default=None, min_length=2, max_length=2),
    day: Date | None = Query(default=None),
) -> dict[str, Any]:
    """Return all 12 ENISA ECSF role profiles with optional country/day demand and supply."""
    if day is not None:
        _validate_day(day)
    country_code = _validate_country(country) if country is not None else None

    session = _session_if_ready(request)
    role_demand: dict[str, int] = {}
    role_supply: dict[str, int] = {}

    if country_code is not None and day is not None and session is not None:
        role_demand = _counts_by_kind(session, "demand_by_day", country_code, day)["role"]
        role_supply = _counts_by_kind(session, "supply_by_day", country_code, day)["role"]

    catalog = load_role_catalog()
    items: list[dict[str, Any]] = []
    for profile in catalog["profiles"]:
        skills = catalog["skills_for_role"].get(profile.role, ())
        certs = catalog["certs_for_role"].get(profile.role, ())
        items.append(
            {
                "role": profile.role,
                "slug": profile.slug,
                "focus_area": profile.focus_area,
                "summary": profile.summary,
                "skills_count": len(skills),
                "certifications_count": len(certs),
                "top_skills": [s["skill"] for s in skills[:5]],
                "demand": role_demand.get(profile.role),
                "supply": role_supply.get(profile.role),
            }
        )

    return {
        "country": country_code,
        "day": day.isoformat() if day is not None else None,
        "cassandra_connected": session is not None,
        "countries": [{"code": code, "name": name} for code, name in COUNTRIES.items()],
        "roles": items,
    }


@router.get("/roles/{role_name}")
def get_role_detail(
    request: Request,
    role_name: str,
    country: str = Query(default="DE", min_length=2, max_length=2),
    day: Date | None = Query(default=None),
) -> dict[str, Any]:
    """Return detailed skills, certifications, related roles, and programmes for one ECSF role."""
    profile = resolve_role(role_name)
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown ECSF role {role_name!r}",
        )

    country_code = _validate_country(country)
    selected_day = day if day is not None else datetime.now(timezone.utc).date()
    _validate_day(selected_day)

    session = _session_if_ready(request)
    demand_counts = _counts_by_kind(session, "demand_by_day", country_code, selected_day)
    supply_counts = _counts_by_kind(session, "supply_by_day", country_code, selected_day)

    catalog = load_role_catalog()
    raw_skills = catalog["skills_for_role"].get(profile.role, ())
    skills = [
        {
            "skill": entry["skill"],
            "nice_ids": entry["nice_ids"],
            "aliases": entry["aliases"],
            "other_roles": [r for r in entry["ecsf_roles"] if r != profile.role],
            "demand": demand_counts["skill"].get(entry["skill"]),
            "supply": supply_counts["skill"].get(entry["skill"]),
        }
        for entry in raw_skills
    ]
    skills.sort(
        key=lambda item: (
            -(item["demand"] if item["demand"] is not None else -1),
            item["skill"].casefold(),
        )
    )

    raw_certs = catalog["certs_for_role"].get(profile.role, ())
    certifications = [
        {
            "certification": entry["certification"],
            "issuer": entry["issuer"],
            "aliases": entry["aliases"],
            "demand": demand_counts["certification"].get(entry["certification"]),
            "supply": supply_counts["certification"].get(entry["certification"]),
        }
        for entry in raw_certs
    ]
    certifications.sort(
        key=lambda item: (
            -(item["demand"] if item["demand"] is not None else -1),
            item["certification"].casefold(),
        )
    )

    # Related roles: prefer Cassandra batch role_relations; fall back to taxonomy Jaccard overlap.
    db_relations = _read_optional(
        session,
        "SELECT related_role, overlap FROM role_relations WHERE role = %s LIMIT 25",
        (profile.role,),
    )
    taxonomy_overlaps = {
        item["role"]: item for item in taxonomy_related_roles(profile.role)
    }
    if db_relations:
        related_roles: list[dict[str, Any]] = []
        for row in db_relations:
            rel_name = getattr(row, "related_role", None)
            if not rel_name:
                continue
            rel_profile = resolve_role(rel_name)
            tax_info = taxonomy_overlaps.get(rel_name, {})
            related_roles.append(
                {
                    "role": rel_profile.role if rel_profile else rel_name,
                    "slug": rel_profile.slug if rel_profile else rel_name.lower().replace(" ", "-"),
                    "focus_area": rel_profile.focus_area if rel_profile else "Cybersecurity Practice",
                    "overlap": round(float(getattr(row, "overlap", 0.0) or 0.0), 4),
                    "shared_skills": tax_info.get("shared_skills", []),
                    "source": "cassandra",
                }
            )
        related_roles.sort(key=lambda item: (-item["overlap"], item["role"].casefold()))
    else:
        related_roles = list(taxonomy_overlaps.values())

    # Education programmes mapped to this ECSF role in the selected country.
    programme_rows = _read_optional(
        session,
        "SELECT programme_id, name, institution, level, ecsf_roles, url "
        "FROM education_programmes WHERE country = %s LIMIT 250",
        (country_code,),
    )
    programmes = [
        {
            "programme_id": row.programme_id,
            "name": row.name,
            "institution": row.institution,
            "level": row.level,
            "ecsf_roles": sorted(row.ecsf_roles or ()),
            "url": row.url,
        }
        for row in programme_rows
        if profile.role in (row.ecsf_roles or ())
    ]

    return {
        "role": profile.role,
        "slug": profile.slug,
        "focus_area": profile.focus_area,
        "summary": profile.summary,
        "country": country_code,
        "country_name": COUNTRIES[country_code],
        "day": selected_day.isoformat(),
        "cassandra_connected": session is not None,
        "demand": demand_counts["role"].get(profile.role),
        "supply": supply_counts["role"].get(profile.role),
        "skills": skills,
        "certifications": certifications,
        "related_roles": related_roles,
        "programmes": programmes,
    }