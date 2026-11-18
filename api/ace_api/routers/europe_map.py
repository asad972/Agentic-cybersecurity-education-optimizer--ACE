"""M4 issue #37: Europe map and country panel, auto-discovered by the API."""
from datetime import date as Date, datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from ..map_data import COUNTRIES, ITEM_METRICS, METRICS, WORLD_METRICS, MapReader, MapResponse, Metric

router = APIRouter(tags=["M4 Europe map"])


def _day(day: Date):
    if not Date(2020, 1, 1) <= day <= datetime.now(timezone.utc).date():
        raise HTTPException(422, "Choose a date from 2020-01-01 through today (UTC)")


def _reader(request):
    database = request.app.state.database
    if not database.ready():
        raise HTTPException(503, "Cassandra is unavailable")
    try:
        return MapReader(database.session)
    except RuntimeError as error:
        raise HTTPException(503, "Cassandra is unavailable") from error


@router.get("/map/meta")
def metadata():
    """Reference labels only; these do not assert data coverage or availability."""
    return {
        "countries": [{"code": code, "name": name} for code, name in COUNTRIES.items()],
        "metrics": [{"id": code, "label": values[0], "needs_item": code in ITEM_METRICS}
                    for code, values in METRICS.items()],
        "date_min": "2020-01-01", "date_max": datetime.now(timezone.utc).date().isoformat(),
    }


@router.get("/map", response_model=MapResponse)
def map_data(request: Request, metric: Metric, date: Date,
             item: str | None = Query(default=None, max_length=200)):
    _day(date)
    item = item.strip() if item else None
    if metric in ITEM_METRICS and not item:
        raise HTTPException(422, "Demand, Supply and Skills Gap require an exact skill name in item")
    if metric not in ITEM_METRICS:
        item = None
    values = _reader(request).values(metric, date, item)
    _, unit, period, note = METRICS[metric]
    # Every Europe-map country is listed, with null where nothing is stored.
    data = [{"country": country, "value": values.get(country)} for country in COUNTRIES]
    if metric in WORLD_METRICS:
        # The world threat map needs the rest of the world too: every other
        # country with stored observations follows, ordered by code.
        data += [{"country": country, "value": value}
                 for country, value in sorted(values.items()) if country not in COUNTRIES]
    return {"metric": metric, "date": date, "item": item, "unit": unit, "period": period,
            "note": note, "data": data}


@router.get("/map/country")
def country_panel(request: Request, country: str, date: Date):
    _day(date)
    country = country.strip().upper()
    if country not in COUNTRIES:
        raise HTTPException(422, "Choose a country from /map/meta (EU27, EEA, UK or Switzerland)")
    return _reader(request).country(country, date)
