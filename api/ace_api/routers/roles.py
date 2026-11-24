"""M1 issue #14: ECSF Role Explorer endpoints, auto-discovered by the API.

Every endpoint reads Cassandra with prepared statements, one bounded
partition per query. Driver classes are never imported here, so the module
loads on Windows and in tests that use a fake session.
"""

from __future__ import annotations

from datetime import date as Date, datetime, timedelta, timezone
import logging
from threading import Lock
from typing import Any
import weakref

from fastapi import APIRouter, HTTPException, Query, Request

from ace.taxonomy import TaxonomyError

try:
    from cassandra import DriverException
except ImportError:
    DriverException = ()  # type: ignore[assignment,misc]

from ..map_data import COUNTRIES, MapResponse
from ..roles_data import load_role_catalog, resolve_role, taxonomy_related_roles

log = logging.getLogger(__name__)
router = APIRouter(tags=["M1 Role explorer"])

ITEMS_LIMIT = 5000
RELATIONS_LIMIT = 25
PROGRAMMES_LIMIT = 250
LATEST_DAY_LOOKBACK = 31

CQL = {
    "demand_day": f"SELECT kind, item, count FROM demand_by_day WHERE country = ? AND day = ? LIMIT {ITEMS_LIMIT}",
    "supply_day": f"SELECT kind, item, count FROM supply_by_day WHERE country = ? AND day = ? LIMIT {ITEMS_LIMIT}",
    "role_map": "SELECT country, count FROM demand_map WHERE kind = ? AND item = ? AND day = ? LIMIT 100",
    "relations": f"SELECT related_role, overlap FROM role_relations WHERE role = ? LIMIT {RELATIONS_LIMIT}",
    "programmes": (
        "SELECT programme_id, name, institution, level, ecsf_roles, url "
        f"FROM education_programmes WHERE country = ? LIMIT {PROGRAMMES_LIMIT}"
    ),
    "job_ads_day": "SELECT country FROM job_ads_by_day WHERE day = ? LIMIT 1",
    "replay_days": "SELECT event_date FROM replay_position LIMIT 50",
}

_prepared = weakref.WeakKeyDictionary()
_prepared_lock = Lock()


class _Unavailable(Exception):
    pass


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


def _session(request: Request):
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise _Unavailable()
    try:
        return database.session
    except RuntimeError:
        if database.ready():
            return database.session
        raise _Unavailable() from None


def _statement(session, name: str):
    with _prepared_lock:
        statements = _prepared.setdefault(session, {})
        if name not in statements:
            statements[name] = session.prepare(CQL[name])
        return statements[name]


def _read(request: Request, reads):
    """Run reads(query) with prepared statements and timeout=5; 503 on database errors."""
    try:
        session = _session(request)

        def query(name: str, *values: Any) -> list[Any]:
            return list(session.execute(_statement(session, name), values, timeout=5))

        return reads(query)
    except HTTPException:
        raise
    except (DriverException, _Unavailable) as error:
        log.warning("Role Explorer Cassandra read failed (%s)", type(error).__name__)
        raise HTTPException(status_code=503, detail="Cassandra is unavailable") from None
    except TaxonomyError as error:
        log.warning("Role Explorer taxonomy unavailable (%s)", type(error).__name__)
        raise HTTPException(status_code=503, detail="Skill taxonomy is unavailable") from None


def _as_date(value: Any) -> Date:
    return value if isinstance(value, Date) else value.date()


def _resolve_default_day(query) -> Date:
    """Find the newest day with stored job ads (from today or replay_position), else today UTC."""
    today = datetime.now(timezone.utc).date()
    starts = {today}
    starts.update(
        _as_date(row.event_date)
        for row in query("replay_days")
        if getattr(row, "event_date", None) is not None
    )
    checked: set[Date] = set()
    for start in sorted(starts, reverse=True):
        for back in range(LATEST_DAY_LOOKBACK + 1):
            candidate = start - timedelta(days=back)
            if candidate in checked:
                continue
            checked.add(candidate)
            if query("job_ads_day", candidate):
                return candidate
    return today


def _counts_by_kind(
    query,
    statement_name: str,
    country: str,
    day: Date,
) -> dict[str, dict[str, int]]:
    rows = query(statement_name, country, day)
    if len(rows) >= ITEMS_LIMIT:
        log.warning(
            "%s for %s on %s hit the %d-row limit; counts may be truncated",
            statement_name,
            country,
            day,
            ITEMS_LIMIT,
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

    def reads(query):
        if country_code is not None and day is not None:
            demand = _counts_by_kind(query, "demand_day", country_code, day)["role"]
            supply = _counts_by_kind(query, "supply_day", country_code, day)["role"]
            return demand, supply
        # Still verify Cassandra connectivity when no country/day filter is passed.
        query("replay_days")
        return {}, {}

    role_demand, role_supply, catalog = _read(
        request,
        lambda query: (*reads(query), load_role_catalog()),
    )
    items: list[dict[str, Any]] = []
    for profile in catalog["profiles"]:
        skills = catalog["skills_for_role"].get(profile.role, ())
        certs = catalog["certs_for_role"].get(profile.role, ())
        items.append(
            {
                "role": profile.role,
                "slug": profile.slug,
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
        "cassandra_connected": True,
        "countries": [{"code": code, "name": name} for code, name in COUNTRIES.items()],
        "roles": items,
    }


@router.get("/roles/{role_name}/map", response_model=MapResponse)
def get_role_demand_map(
    request: Request,
    role_name: str,
    day: Date | None = Query(default=None),
) -> dict[str, Any]:
    """Return daily demand per country for an ECSF role from demand_map (kind='role')."""
    try:
        profile = resolve_role(role_name)
    except TaxonomyError:
        raise HTTPException(status_code=503, detail="Skill taxonomy is unavailable") from None
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown ECSF role {role_name!r}",
        )
    if day is not None:
        _validate_day(day)

    def reads(query):
        selected_day = day if day is not None else _resolve_default_day(query)
        rows = query("role_map", "role", profile.role, selected_day)
        return selected_day, rows

    selected_day, rows = _read(request, reads)
    counts = {
        row.country: int(row.count)
        for row in rows
        if getattr(row, "country", None) in COUNTRIES and getattr(row, "count", None) is not None
    }
    data = [{"country": code, "value": counts.get(code)} for code in COUNTRIES]
    return {
        "metric": "demand",
        "date": selected_day,
        "item": profile.role,
        "unit": "job ads",
        "period": "daily",
        "note": "Daily estimated job advertisements mapped to this ECSF role per country.",
        "data": data,
    }


@router.get("/roles/{role_name}")
def get_role_detail(
    request: Request,
    role_name: str,
    country: str = Query(default="DE", min_length=2, max_length=2),
    day: Date | None = Query(default=None),
) -> dict[str, Any]:
    """Return detailed skills, certifications, related roles, and programmes for one ECSF role."""
    try:
        profile = resolve_role(role_name)
    except TaxonomyError:
        raise HTTPException(status_code=503, detail="Skill taxonomy is unavailable") from None
    if profile is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown ECSF role {role_name!r}",
        )

    country_code = _validate_country(country)
    if day is not None:
        _validate_day(day)

    def reads(query):
        selected_day = day if day is not None else _resolve_default_day(query)
        demand_counts = _counts_by_kind(query, "demand_day", country_code, selected_day)
        supply_counts = _counts_by_kind(query, "supply_day", country_code, selected_day)
        db_relations = query("relations", profile.role)
        programme_rows = query("programmes", country_code)
        if len(programme_rows) >= PROGRAMMES_LIMIT:
            log.warning(
                "education_programmes for %s hit the %d-row limit; role programmes may be truncated",
                country_code,
                PROGRAMMES_LIMIT,
            )
        return selected_day, demand_counts, supply_counts, db_relations, programme_rows

    selected_day, demand_counts, supply_counts, db_relations, programme_rows, catalog = _read(
        request,
        lambda query: (*reads(query), load_role_catalog()),
    )
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
                    "overlap": round(float(getattr(row, "overlap", 0.0) or 0.0), 4),
                    "shared_skills": tax_info.get("shared_skills", []),
                    "source": "cassandra",
                }
            )
        related_roles.sort(key=lambda item: (-item["overlap"], item["role"].casefold()))
    else:
        related_roles = list(taxonomy_overlaps.values())

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
        "summary": profile.summary,
        "country": country_code,
        "country_name": COUNTRIES[country_code],
        "day": selected_day.isoformat(),
        "cassandra_connected": True,
        "demand": demand_counts["role"].get(profile.role),
        "supply": supply_counts["role"].get(profile.role),
        "skills": skills,
        "certifications": certifications,
        "related_roles": related_roles,
        "programmes": programmes,
    }
