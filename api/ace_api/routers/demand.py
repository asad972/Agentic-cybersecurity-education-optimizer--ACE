"""Skills demand dashboard endpoints (M2, issue #23).

Every endpoint reads Cassandra with prepared statements, one bounded
partition per query. Only the session passed in by the app is used, so the
endpoints run in tests with a fake session.
"""
from datetime import date, datetime, timedelta, timezone
import logging
from threading import Lock
from time import monotonic
from typing import Annotated, Literal
import weakref

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..countries import COUNTRIES

log = logging.getLogger(__name__)

router = APIRouter(tags=["Skills demand"])

# Skills Scope: EU27, the EEA countries outside the EU, the UK and Switzerland.
# The same list as the Europe map and /map/meta.
SKILLS_SCOPE = tuple(COUNTRIES)
# How many days before a starting day /demand/latest-day looks for data.
LATEST_DAY_LOOKBACK = 31
# How many days before the requested day /top10 looks for a Top 10.
TOP10_LOOKBACK = 30
# Most items read from one demand_by_day partition (one Country, day and kind).
ITEMS_LIMIT = 5000
# Seconds one read may take, as in map_data.
READ_TIMEOUT = 5
# Seconds all reads of one request may take together. Some endpoints read up to 32
# partitions one after another, so without a total a slow Cassandra could keep a
# worker thread busy for minutes. Kept below nginx's 30 s proxy_read_timeout.
REQUEST_BUDGET = 25
# Seconds preparing one statement may take: the session's default_timeout in
# database.py. A prepare only starts when this much of the budget is left.
PREPARE_TIMEOUT = 5

CQL = {
    "top10": "SELECT rank, item, count FROM top10 WHERE country = ? AND day = ? AND kind = ? LIMIT 10",
    "map": "SELECT country, count FROM demand_map WHERE kind = ? AND item = ? AND day = ? LIMIT 100",
    "items": f"SELECT item, count FROM demand_by_day WHERE country = ? AND day = ? AND kind = ? LIMIT {ITEMS_LIMIT}",
    "job_ads_day": "SELECT country FROM job_ads_by_day WHERE day = ? LIMIT 1",
    # One row per replayed dataset, so this table stays a handful of rows.
    "replay_days": "SELECT event_date FROM replay_position LIMIT 50",
}

Kind = Literal["skill", "certification", "role"]
TopKind = Literal["skill", "certification"]
Country = Annotated[str, Query(pattern=r"^[A-Z]{2}$", description="ISO 3166-1 alpha-2 code, e.g. DE")]
Day = Annotated[str, Query(alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$", description="Day as YYYY-MM-DD")]
Item = Annotated[str, Query(min_length=1, max_length=200, description="Skill Taxonomy entry, e.g. SIEM")]


class RankedItem(BaseModel):
    rank: int
    item: str
    count: int


class Top10(BaseModel):
    country: str
    kind: TopKind
    requested_date: date
    # Last day of the 30-day window that is returned; null when no Top 10 was found.
    date: date | None
    items: list[RankedItem]


class CountryCount(BaseModel):
    country: str
    count: int


class DemandMap(BaseModel):
    kind: Kind
    item: str
    date: date
    values: list[CountryCount]


class ItemCount(BaseModel):
    item: str
    count: int


class DemandItems(BaseModel):
    kind: Kind
    date: date
    items: list[ItemCount]


class LatestDay(BaseModel):
    date: date


class _Unavailable(Exception):
    pass


class _OutOfTime(Exception):
    """The request used up REQUEST_BUDGET before its next read."""


_prepared = weakref.WeakKeyDictionary()
_prepared_lock = Lock()


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be a valid day as YYYY-MM-DD") from None


def _session(request: Request):
    database = request.app.state.database
    try:
        return database.session
    except RuntimeError:
        # No open session: try to reconnect once (ready() limits retries itself).
        if database.ready():
            return database.session
        raise _Unavailable() from None


def _statement(session, name: str, deadline: float):
    # The lock only guards the cache. Preparing is a network round trip, so it runs
    # outside the lock: one slow prepare must not hold up every other request. Two
    # requests may prepare the same statement at once; the first one stored is kept.
    with _prepared_lock:
        statement = _prepared.setdefault(session, {}).get(name)
    if statement is not None:
        return statement
    # Preparing takes up to PREPARE_TIMEOUT seconds that no read timeout covers,
    # so only start it if the budget still has room.
    if deadline - monotonic() < PREPARE_TIMEOUT:
        raise _OutOfTime()
    statement = session.prepare(CQL[name])
    with _prepared_lock:
        return _prepared.setdefault(session, {}).setdefault(name, statement)


def _read(request: Request, reads):
    """Run reads(query) where query(name, *values) returns the rows; 503 on database errors.

    Each read may take READ_TIMEOUT seconds, and all reads together REQUEST_BUDGET seconds.
    """
    deadline = monotonic() + REQUEST_BUDGET
    try:
        session = _session(request)

        def query(name, *values):
            statement = _statement(session, name, deadline)
            # Worked out after the prepare, so its time counts against the budget.
            left = deadline - monotonic()
            if left <= 0:
                raise _OutOfTime()
            return list(session.execute(statement, values, timeout=min(READ_TIMEOUT, left)))

        return reads(query)
    except HTTPException:
        raise
    except Exception as error:
        log.warning("Demand read failed (%s)", type(error).__name__)
        raise HTTPException(status_code=503, detail="Cassandra is unavailable") from None


@router.get("/top10", response_model=Top10)
def top10(request: Request, country: Country, kind: TopKind, day: Day):
    """Top 10 Skills or Certifications in a Country over the 30 days ending on a day (S3).

    When the requested day has no Top 10 rows (no job ads that day, or none mentioning
    a Certification), the newest earlier day within 30 days is returned instead;
    `date` says which day the list is for. Counts are 30-day totals.
    """
    on = _parse_day(day)

    def reads(query):
        # S3 writes no Top 10 rows for a day without job ads, or for an empty list,
        # so step back to the newest earlier day.
        for back in range(TOP10_LOOKBACK + 1):
            found = on - timedelta(days=back)
            rows = query("top10", country, found, kind)
            if rows:
                return found, rows
        return None, []

    found, rows = _read(request, reads)
    items = sorted((RankedItem(rank=r.rank, item=r.item, count=r.count or 0) for r in rows), key=lambda i: i.rank)
    return Top10(country=country, kind=kind, requested_date=on, date=found, items=items)


@router.get("/demand/map", response_model=DemandMap)
def demand_map(request: Request, kind: Kind, item: Item, day: Day):
    """Demand for one Skill, Certification or role per Country on one day (S4)."""
    on = _parse_day(day)
    rows = _read(request, lambda query: query("map", kind, item, on))
    values = sorted((CountryCount(country=r.country, count=r.count or 0) for r in rows), key=lambda v: v.country)
    return DemandMap(kind=kind, item=item, date=on, values=values)


@router.get("/demand/items", response_model=DemandItems)
def demand_items(request: Request, kind: Kind, day: Day):
    """Every item with Demand on one day, summed over the Skills Scope, most demanded first.

    Feeds the Skill filter. Reads one demand_by_day partition per Country in the Skills Scope.
    """
    on = _parse_day(day)

    def reads(query):
        totals: dict[str, int] = {}
        for country in SKILLS_SCOPE:
            rows = query("items", country, on, kind)
            if len(rows) >= ITEMS_LIMIT:
                # The Skill Taxonomy is closed and far smaller than the limit, so this
                # should never happen; if it does, the totals below are incomplete.
                log.warning("demand_by_day %s %s %s hit the %d-row limit", country, on, kind, ITEMS_LIMIT)
            for row in rows:
                totals[row.item] = totals.get(row.item, 0) + (row.count or 0)
        return totals

    totals = _read(request, reads)
    items = [ItemCount(item=item, count=count)
             for item, count in sorted(totals.items(), key=lambda pair: (-pair[1], pair[0].casefold()))]
    return DemandItems(kind=kind, date=on, items=items)


@router.get("/demand/latest-day", response_model=LatestDay, responses={404: {"description": "No Demand data found"}})
def latest_day(request: Request):
    """Newest day with job ads counted by S4, used as the dashboard's default date.

    Starts from today (live job ads) and from the last replayed day of each dataset
    (replay_position), and looks back up to 31 days from each start.
    """
    def reads(query):
        starts = {datetime.now(timezone.utc).date()}
        starts.update(_as_date(r.event_date) for r in query("replay_days") if r.event_date is not None)
        checked = set()
        for start in sorted(starts, reverse=True):
            for back in range(LATEST_DAY_LOOKBACK + 1):
                on = start - timedelta(days=back)
                if on in checked:
                    continue
                checked.add(on)
                if query("job_ads_day", on):
                    return on
        return None

    found = _read(request, reads)
    if found is None:
        raise HTTPException(status_code=404, detail="No Demand data found")
    return LatestDay(date=found)


def _as_date(value) -> date:
    # The driver returns cassandra.util.Date for date columns; date() converts it.
    return value if isinstance(value, date) else value.date()
