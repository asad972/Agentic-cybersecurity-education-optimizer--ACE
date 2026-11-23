"""Validated configuration for the forecasting job."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ace.batch import CassandraSettings, SKILLS_SCOPE


@dataclass(frozen=True, slots=True)
class ForecastConfig(CassandraSettings):
    horizon_months: int = 12
    seasonal_naive_min_months: int = 15
    advanced_min_months: int = 27
    interval: float = 0.95

    def __post_init__(self) -> None:
        CassandraSettings.__post_init__(self)
        if self.horizon_months != 12:
            raise ValueError("Forecast horizon must be 12 months")
        if self.seasonal_naive_min_months < 13:
            raise ValueError("Seasonal-naive history threshold must be at least 13")
        if self.advanced_min_months <= self.seasonal_naive_min_months:
            raise ValueError("Advanced history threshold must exceed fallback threshold")
        if not 0.0 < self.interval < 1.0:
            raise ValueError("Forecast interval must be between zero and one")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ForecastConfig":
        return cls(**cls._environment_fields(env))
