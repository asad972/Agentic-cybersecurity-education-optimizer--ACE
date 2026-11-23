"""M4 issue #40: threat landscape, auto-discovered by the API.

The world map itself uses GET /map?metric=threats (routers/europe_map.py).
"""
from datetime import date as Date
import re

from fastapi import APIRouter, HTTPException, Query, Request

from ..map_data import check_day
from ..threat_data import MAX_DAYS, HourlyThreats, ThreatReader

router = APIRouter(tags=["M4 threat landscape"])
COUNTRY = re.compile(r"[A-Z]{2}")


@router.get("/threats/hourly", response_model=HourlyThreats)
def hourly(request: Request, country: str, first: Date = Query(alias="from"), last: Date | None = Query(default=None, alias="to")):
    """Hourly threat observations for one country, with S8's bursts, from one day through another.

    `from` and `to` are UTC days and both are included; `to` defaults to `from`, and at most
    31 days are read. Threats are measured worldwide, so any ISO 3166-1 alpha-2 code is accepted.
    """
    last = last or first
    check_day(first)
    check_day(last)
    if last < first:
        raise HTTPException(422, "to must not be before from")
    if (last - first).days >= MAX_DAYS:
        raise HTTPException(422, f"Choose at most {MAX_DAYS} days")
    country = country.strip().upper()
    if not COUNTRY.fullmatch(country):
        raise HTTPException(422, "country must be an ISO 3166-1 alpha-2 code such as HU")
    database = request.app.state.database
    if not database.ready():
        raise HTTPException(503, "Cassandra is unavailable")
    try:
        session = database.session
    except RuntimeError as error:
        raise HTTPException(503, "Cassandra is unavailable") from error
    return ThreatReader(session).hourly(country, first, last)
