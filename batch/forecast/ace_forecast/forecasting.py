"""History-tier policy, leakage-free evaluation, and final refitting."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import math
from typing import Sequence

from .config import ForecastConfig
from .models import (
    Forecaster,
    LightGBMForecaster,
    ModelFitError,
    SarimaForecaster,
    SeasonalNaiveForecaster,
)
from .series import add_months
from .types import Evaluation, ForecastPoint, ForecastResult, MonthlySeries, SkippedSeries


def evaluate(actual: Sequence[float], predicted: Sequence[float]) -> Evaluation:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("Actual and predicted values must have the same nonzero length")
    errors = [abs(float(expected) - float(observed)) for expected, observed in zip(actual, predicted, strict=True)]
    mae = sum(errors) / len(errors)
    percentages = [
        abs(float(expected) - float(observed)) / abs(float(expected)) * 100.0
        for expected, observed in zip(actual, predicted, strict=True)
        if expected != 0
    ]
    return Evaluation(mae, sum(percentages) / len(percentages) if percentages else None)


def _points(last_month: date, forecast) -> tuple[ForecastPoint, ...]:
    return tuple(
        ForecastPoint(
            add_months(last_month, index).strftime("%Y-%m"),
            forecast.predicted[index - 1],
            forecast.lower[index - 1],
            forecast.upper[index - 1],
        )
        for index in range(1, len(forecast.predicted) + 1)
    )


def forecast_series(
    series: MonthlySeries,
    config: ForecastConfig = ForecastConfig(),
    candidates: Mapping[str, Forecaster] | None = None,
) -> ForecastResult | SkippedSeries:
    history = len(series.values)
    if history < config.seasonal_naive_min_months:
        return SkippedSeries(series.region, series.skill, history, "fewer_than_15_months")

    fallback = SeasonalNaiveForecaster(interval=config.interval)
    if history < config.advanced_min_months:
        fitted = fallback.forecast(series.months, series.values, config.horizon_months)
        return ForecastResult(
            series.region,
            series.skill,
            fitted.model,
            history,
            None,
            _points(series.months[-1], fitted),
        )

    models: dict[str, Forecaster] = dict(candidates or {
        "sarima": SarimaForecaster(interval=config.interval),
        "lightgbm": LightGBMForecaster(interval=config.interval),
    })
    train_months, holdout_months = series.months[:-3], series.months[-3:]
    train_values, actual = series.values[:-3], series.values[-3:]
    scored: dict[str, Evaluation] = {}
    for name in ("sarima", "lightgbm"):
        model = models.get(name)
        if model is None:
            continue
        try:
            candidate = model.forecast(train_months, train_values, len(holdout_months))
            scored[name] = evaluate(actual, candidate.predicted)
        except ModelFitError:
            continue

    order = sorted(scored, key=lambda name: (scored[name].mae, 0 if name == "sarima" else 1))
    for name in order:
        try:
            fitted = models[name].forecast(series.months, series.values, config.horizon_months)
            return ForecastResult(
                series.region,
                series.skill,
                name,
                history,
                scored[name],
                _points(series.months[-1], fitted),
            )
        except ModelFitError:
            continue

    fitted = fallback.forecast(series.months, series.values, config.horizon_months)
    fallback_evaluation = evaluate(
        actual,
        fallback.forecast(train_months, train_values, len(holdout_months)).predicted,
    )
    return ForecastResult(
        series.region,
        series.skill,
        fitted.model,
        history,
        fallback_evaluation,
        _points(series.months[-1], fitted),
    )
