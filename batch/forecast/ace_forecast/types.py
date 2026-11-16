"""Immutable values shared across the forecasting job."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class DemandPoint:
    day: date
    count: int


@dataclass(frozen=True, slots=True)
class MonthlySeries:
    region: str
    skill: str
    months: tuple[date, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.months) != len(self.values):
            raise ValueError("months and values must have the same length")
        if any(month.day != 1 for month in self.months):
            raise ValueError("months must use the first calendar day")
        if tuple(sorted(self.months)) != self.months or len(set(self.months)) != len(self.months):
            raise ValueError("months must be unique and ordered")
        if any(value < 0 for value in self.values):
            raise ValueError("Demand values must be nonnegative")


@dataclass(frozen=True, slots=True)
class Evaluation:
    mae: float
    mape: float | None


@dataclass(frozen=True, slots=True)
class ModelForecast:
    model: str
    predicted: tuple[float, ...]
    lower: tuple[float, ...]
    upper: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    target_month: str
    predicted: float
    lower: float
    upper: float


@dataclass(frozen=True, slots=True)
class ForecastResult:
    region: str
    skill: str
    model: str
    history_months: int
    evaluation: Evaluation | None
    points: tuple[ForecastPoint, ...]


@dataclass(frozen=True, slots=True)
class SkippedSeries:
    region: str
    skill: str
    history_months: int
    reason: str


@dataclass(frozen=True, slots=True)
class ForecastRow:
    region: str
    skill: str
    target_month: str
    predicted: float
    lower: float
    upper: float
    model: str
    trained_at: datetime
