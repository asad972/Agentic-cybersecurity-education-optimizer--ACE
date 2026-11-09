"""Read-only API endpoints for the M5 education and workforce dashboard."""

import logging
from datetime import date
from typing import Any

from cassandra import DriverException
from fastapi import APIRouter, HTTPException, Query, Request

log = logging.getLogger(__name__)
router = APIRouter(tags=["M5 education and workforce"])

def _session(request: Request):
    """Return the connected Cassandra session or report service unavailability."""
    database = request.app.state.database
    if not database.ready():
        raise HTTPException(status_code=503, detail="Cassandra is unavailable")
    try:
        return database.session
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="Cassandra is unavailable") from error


def _read(request: Request, query: str, parameters: tuple[Any, ...]):
    """Execute a bounded read and avoid exposing driver details to API clients."""
    try:
        return _session(request).execute(query, parameters)
    except DriverException as error:
        log.warning("M5 Cassandra read failed (%s)", type(error).__name__)
        raise HTTPException(status_code=503, detail="Cassandra read is unavailable") from error


@router.get("/education")
def get_education(
    request: Request,
    country: str = Query(pattern=r"^[A-Z]{2}$"),
    limit: int = Query(default=100, ge=1, le=500),
    day: date | None = Query(default=None),
) -> dict[str, Any]:
    """Return programme, workforce, and optional role-demand rows for a country."""
    programmes = _read(
        request,
        "SELECT programme_id, name, institution, level, ecsf_roles, url "
        "FROM education_programmes WHERE country = %s LIMIT %s",
        (country, limit),
    )
    workforce = _read(
        request,
        "SELECT year, workforce_size, workforce_gap, source "
        "FROM workforce_baseline WHERE country = %s LIMIT %s",
        (country, limit),
    )
    demand_by_role: dict[str, int] = {}
    if day is not None:
        demand = _read(
            request,
            "SELECT item, count FROM demand_by_day "
            "WHERE country = %s AND day = %s AND kind = %s LIMIT 500",
            (country, day, "role"),
        )
        demand_by_role = {row.item: row.count for row in demand}
    else:
        demand = []

    programme_counts: dict[str, int] = {}
    for row in programmes:
        for role in set(row.ecsf_roles or []):
            programme_counts[role] = programme_counts.get(role, 0) + 1

    role_comparison = [
        {
            "role": role,
            "programme_count": programme_counts.get(role, 0),
            "demand_count": (
                demand_by_role.get(role, 0) if demand else None
            ),
        }
        for role in sorted(programme_counts.keys() | demand_by_role.keys())
    ]
    return {
        "country": country,
        "day": day.isoformat() if day is not None else None,
        "demand_available": bool(demand),
        "programmes": [
            {
                "programme_id": row.programme_id,
                "name": row.name,
                "institution": row.institution,
                "level": row.level,
                "ecsf_roles": sorted(row.ecsf_roles or []),
                "url": row.url,
            }
            for row in programmes
        ],
        "workforce": [
            {
                "year": row.year,
                "workforce_size": row.workforce_size,
                "workforce_gap": row.workforce_gap,
                "source": row.source,
            }
            for row in workforce
        ],
        "role_comparison": role_comparison,
    }


@router.get("/emerging")
def get_emerging_skills(
    request: Request,
    country: str = Query(pattern=r"^[A-Z]{2}$"),
    day: date = Query(...),
) -> dict[str, Any]:
    """Return the S9 snapshot for one country and day partition."""
    rows = _read(
        request,
        "SELECT mention, count, max_error FROM emerging_skills "
        "WHERE country = %s AND day = %s LIMIT 50",
        (country, day),
    )
    return {
        "country": country,
        "day": day.isoformat(),
        "items": [
            {"mention": row.mention, "count": row.count, "max_error": row.max_error}
            for row in rows
        ],
    }


@router.get("/companies")
def get_hiring_companies(
    request: Request,
    country: str = Query(pattern=r"^[A-Z]{2}$"),
    day: date = Query(...),
) -> dict[str, Any]:
    """Return the S10 company estimates for one country and day."""
    rows = _read(
        request,
        "SELECT companies_day, companies_30d, missing_company "
        "FROM companies_by_day WHERE day = %s AND country = %s LIMIT 1",
        (day, country),
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No hiring-company snapshot exists for {country} on {day}",
        )
    row = rows[0]
    return {
        "country": country,
        "day": day.isoformat(),
        "companies_day": row.companies_day,
        "companies_30d": row.companies_30d,
        "missing_company": row.missing_company,
    }


@router.get("/platforms")
def get_platforms(request: Request) -> dict[str, Any]:
    """Return evidence for every platform partition currently in Cassandra."""
    partitions = _read(
        request, "SELECT DISTINCT platform FROM platform_analysis", ()
    )
    platforms = tuple(row.platform for row in partitions)
    if not platforms:
        return {"items": []}

    placeholders = ", ".join("%s" for _ in platforms)
    results = _read(
        request,
        "SELECT platform, feature, supported, notes, analysed_at "
        f"FROM platform_analysis WHERE platform IN ({placeholders}) LIMIT 500",
        platforms,
    )
    return {
        "items": [
            {
                "platform": row.platform,
                "feature": row.feature,
                "supported": row.supported,
                "notes": row.notes,
                "analysed_at": row.analysed_at,
            }
            for row in results
        ]
    }
