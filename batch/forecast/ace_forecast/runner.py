"""Orchestrate fixed-scope reads, forecasts, metrics, and idempotent writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import logging
from typing import Callable, Iterable

from .config import ForecastConfig, SKILLS_SCOPE
from .forecasting import forecast_series
from .repository import ForecastRepository
from .series import eu_series, monthly_series
from .types import ForecastResult, ForecastRow, MonthlySeries, SkippedSeries


LOGGER = logging.getLogger("ace_forecast")


@dataclass(frozen=True, slots=True)
class RunSummary:
    skills: int
    series_with_data: int
    forecast_series: int
    skipped_series: int
    rows_written: int


def _metric(event: str, **values) -> None:
    LOGGER.info(json.dumps({"event": event, **values}, sort_keys=True, separators=(",", ":")))


def run_forecasts(
    repository: ForecastRepository,
    skills: Iterable[str],
    as_of: date,
    config: ForecastConfig = ForecastConfig(),
    trained_at: datetime | None = None,
    forecast: Callable[[MonthlySeries, ForecastConfig], ForecastResult | SkippedSeries] = forecast_series,
) -> RunSummary:
    skill_names = tuple(skills)
    run_time = trained_at or datetime.now(timezone.utc)
    if run_time.tzinfo is None:
        raise ValueError("trained_at must be timezone-aware")

    output: list[ForecastRow] = []
    data_series = 0
    forecast_count = 0
    skipped_count = 0
    for skill in skill_names:
        by_country: dict[str, MonthlySeries] = {}
        for country in SKILLS_SCOPE:
            series = monthly_series(country, skill, repository.read_daily(country, skill), as_of)
            if series is not None:
                by_country[country] = series
                data_series += 1
                outcome = forecast(series, config)
                if isinstance(outcome, ForecastResult):
                    forecast_count += 1
                    output.extend(_rows(outcome, run_time))
                    _log_result(outcome)
                else:
                    skipped_count += 1
                    _log_skip(outcome)

        regional = eu_series(skill, SKILLS_SCOPE, by_country)
        if regional is not None:
            data_series += 1
            outcome = forecast(regional, config)
            if isinstance(outcome, ForecastResult):
                forecast_count += 1
                output.extend(_rows(outcome, run_time))
                _log_result(outcome)
            else:
                skipped_count += 1
                _log_skip(outcome)

    if not output:
        LOGGER.warning(json.dumps({"event": "forecast_no_output", "skills": len(skill_names)}, sort_keys=True))
    else:
        repository.write(output)
    summary = RunSummary(len(skill_names), data_series, forecast_count, skipped_count, len(output))
    _metric("forecast_summary", **{
        "skills": summary.skills,
        "series_with_data": summary.series_with_data,
        "forecast_series": summary.forecast_series,
        "skipped_series": summary.skipped_series,
        "rows_written": summary.rows_written,
    })
    return summary


def _rows(result: ForecastResult, trained_at: datetime) -> list[ForecastRow]:
    return [
        ForecastRow(
            result.region,
            result.skill,
            point.target_month,
            point.predicted,
            point.lower,
            point.upper,
            result.model,
            trained_at,
        )
        for point in result.points
    ]


def _log_result(result: ForecastResult) -> None:
    _metric(
        "forecast_selected",
        region=result.region,
        skill=result.skill,
        history_months=result.history_months,
        model=result.model,
        mae=result.evaluation.mae if result.evaluation else None,
        mape=result.evaluation.mape if result.evaluation else None,
    )


def _log_skip(result: SkippedSeries) -> None:
    _metric(
        "forecast_skipped",
        region=result.region,
        skill=result.skill,
        history_months=result.history_months,
        reason=result.reason,
    )
