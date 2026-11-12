"""Read-only endpoints for the M3 Supply, Gap, and Forecast dashboard."""

from datetime import datetime, timezone
import logging
from typing import Annotated, Literal

from cassandra import DriverException
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["M3 supply, gap and forecast"])

_READ_TIMEOUT_SECONDS = 5
_GAP_ROW_LIMIT = 500
_FORECAST_ROW_LIMIT = 12
_COUNTRY_PATTERN = r"^[A-Z]{2}$"
_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"

NonNegativeInt = Annotated[int, Field(ge=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]


class _ResponseModel(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)


class GapItem(_ResponseModel):
    skill: str = Field(min_length=1, max_length=200)
    demand: NonNegativeInt
    supply: NonNegativeInt
    gap_ratio: NonNegativeFloat | None


class GapResponse(_ResponseModel):
    country: str = Field(pattern=_COUNTRY_PATTERN)
    month: str = Field(pattern=_MONTH_PATTERN)
    items: tuple[GapItem, ...]


class ForecastItem(_ResponseModel):
    target_month: str = Field(pattern=_MONTH_PATTERN)
    predicted: NonNegativeFloat
    lower: NonNegativeFloat
    upper: NonNegativeFloat
    model: Literal["sarima", "lightgbm", "seasonal_naive"]
    trained_at: datetime

    @field_validator("trained_at")
    @classmethod
    def normalize_trained_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_interval(self):
        if not self.lower <= self.predicted <= self.upper:
            raise ValueError("forecast bounds must contain the prediction")
        return self


class ForecastResponse(_ResponseModel):
    region: str = Field(pattern=_COUNTRY_PATTERN)
    skill: str = Field(min_length=1, max_length=200)
    items: tuple[ForecastItem, ...]


def _session(request: Request):
    database = request.app.state.database
    if not database.ready():
        raise HTTPException(status_code=503, detail="Cassandra is unavailable")
    try:
        return database.session
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="Cassandra is unavailable") from error


def _read(request: Request, query: str, parameters: tuple[object, ...]):
    try:
        return list(
            _session(request).execute(
                query,
                parameters,
                timeout=_READ_TIMEOUT_SECONDS,
            )
        )
    except (DriverException, RuntimeError) as error:
        log.warning("M3 Cassandra read failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail="M3 dashboard storage is unavailable",
        ) from error


def _invalid_stored_data(error: ValidationError):
    log.warning("M3 stored data failed response validation (%s)", type(error).__name__)
    raise HTTPException(
        status_code=503,
        detail="M3 dashboard storage returned invalid data",
    ) from error


@router.get("/gap", response_model=GapResponse)
def get_gap(
    request: Request,
    country: str = Query(pattern=_COUNTRY_PATTERN),
    month: str = Query(pattern=_MONTH_PATTERN),
) -> GapResponse:
    """Return one complete country/month Skills Gap partition."""
    rows = _read(
        request,
        "SELECT skill, demand, supply, gap_ratio FROM skills_gap "
        "WHERE country = %s AND month = %s LIMIT 501",
        (country, month),
    )
    if len(rows) > _GAP_ROW_LIMIT:
        raise HTTPException(
            status_code=503,
            detail="Skills Gap partition exceeds the dashboard read limit",
        )
    try:
        items = tuple(
            sorted(
                (
                    GapItem(
                        skill=row.skill,
                        demand=row.demand,
                        supply=row.supply,
                        gap_ratio=row.gap_ratio,
                    )
                    for row in rows
                ),
                key=lambda item: (item.skill.casefold(), item.skill),
            )
        )
        return GapResponse(country=country, month=month, items=items)
    except ValidationError as error:
        _invalid_stored_data(error)


@router.get("/forecast", response_model=ForecastResponse)
def get_forecast(
    request: Request,
    region: str = Query(pattern=_COUNTRY_PATTERN),
    skill: str = Query(min_length=1, max_length=200),
) -> ForecastResponse:
    """Return the ordered forecast horizon for one region and exact Skill."""
    skill = skill.strip()
    if not skill:
        raise HTTPException(status_code=422, detail="Skill must not be blank")
    rows = _read(
        request,
        "SELECT target_month, predicted, lower, upper, model, trained_at "
        "FROM forecasts WHERE region = %s AND skill = %s LIMIT 13",
        (region, skill),
    )
    if len(rows) > _FORECAST_ROW_LIMIT:
        raise HTTPException(
            status_code=503,
            detail="Forecast partition exceeds the 12-month read limit",
        )
    try:
        items = tuple(
            sorted(
                (
                    ForecastItem(
                        target_month=row.target_month,
                        predicted=row.predicted,
                        lower=row.lower,
                        upper=row.upper,
                        model=row.model,
                        trained_at=row.trained_at,
                    )
                    for row in rows
                ),
                key=lambda item: item.target_month,
            )
        )
        return ForecastResponse(region=region, skill=skill, items=items)
    except ValidationError as error:
        _invalid_stored_data(error)
