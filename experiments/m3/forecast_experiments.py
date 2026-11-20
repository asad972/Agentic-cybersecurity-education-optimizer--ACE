"""Forecast experiments run in the pinned batch dependency environment."""

from __future__ import annotations

from collections import Counter
from datetime import date
import math
from typing import Sequence
import warnings

import numpy as np

from ace_forecast.config import ForecastConfig
from ace_forecast.forecasting import forecast_series
from ace_forecast.models import (
    LightGBMForecaster,
    ModelFitError,
    SarimaForecaster,
    SeasonalNaiveForecaster,
)
from ace_forecast.series import add_months
from ace_forecast.types import ForecastResult, MonthlySeries

from .common import FORECAST_SCENARIOS, MODELS, SEED, rng


def _forecast_values(
    name: str, generator: np.random.Generator, months: int = 84
) -> tuple[float, ...]:
    values: list[float] = []
    for month_index in range(months):
        angle = 2.0 * math.pi * month_index / 12.0
        if name == "stable_seasonality":
            value = 30.0 + 8.0 * math.sin(angle) + generator.normal(0.0, 2.0)
        elif name == "trend_seasonality":
            value = (
                12.0
                + 0.6 * month_index
                + 8.0 * math.sin(angle)
                + generator.normal(0.0, 2.0)
            )
        elif name == "level_change":
            value = (
                20.0
                + 6.0 * math.sin(angle)
                + (25.0 if month_index >= 42 else 0.0)
                + generator.normal(0.0, 2.0)
            )
        elif name == "intermittent":
            value = (
                10.0 + generator.poisson(15.0)
                if generator.random() < 0.45
                else 0.0
            )
        else:
            raise ValueError(f"unknown forecast scenario: {name}")
        values.append(float(max(0, int(round(float(value))))))
    return tuple(values)


def _forecast_metrics(
    actual: Sequence[float],
    predicted: Sequence[float],
    lower: Sequence[float],
    upper: Sequence[float],
) -> dict[str, object]:
    if not (len(actual) == len(predicted) == len(lower) == len(upper)) or not actual:
        raise AssertionError("forecast experiment returned inconsistent lengths")
    errors: list[float] = []
    percentages: list[float] = []
    covered = 0
    widths: list[float] = []
    for expected, middle, low, high in zip(
        actual, predicted, lower, upper, strict=True
    ):
        if not all(
            math.isfinite(float(value)) for value in (expected, middle, low, high)
        ):
            raise AssertionError("forecast experiment returned a non-finite value")
        if low < 0 or middle < 0 or not low <= middle <= high:
            raise AssertionError("forecast experiment returned incoherent bounds")
        errors.append(abs(float(expected) - float(middle)))
        if expected:
            percentages.append(
                abs(float(expected) - float(middle)) / abs(float(expected)) * 100.0
            )
        covered += int(float(low) <= float(expected) <= float(high))
        widths.append(float(high) - float(low))
    return {
        "mae": float(np.mean(errors)),
        "mape": float(np.mean(percentages)) if percentages else None,
        "interval_coverage": covered / len(actual),
        "mean_interval_width": float(np.mean(widths)),
        "predictions": len(actual),
    }


def run_forecast(
    seed: int = SEED,
    *,
    scenarios: Sequence[str] = FORECAST_SCENARIOS,
    origins: Sequence[int] = (48, 60, 72),
) -> dict[str, object]:
    config = ForecastConfig()
    start = date(2018, 1, 1)
    months = tuple(add_months(start, offset) for offset in range(84))
    scenario_results: dict[str, object] = {}
    for scenario_index, scenario in enumerate(scenarios):
        generator = rng(seed, 3, scenario_index)
        values = _forecast_values(scenario, generator)
        collected: dict[str, dict[str, list[float]]] = {
            model: {"actual": [], "predicted": [], "lower": [], "upper": []}
            for model in MODELS
        }
        selections: Counter[str] = Counter()
        for origin in origins:
            if origin < 27 or origin + 12 > len(values):
                raise ValueError(
                    "forecast origin must leave a 12-month evaluation horizon"
                )
            train_months = months[:origin]
            train_values = values[:origin]
            actual = values[origin : origin + 12]
            direct_models = {
                "seasonal_naive": SeasonalNaiveForecaster(interval=config.interval),
                "sarima": SarimaForecaster(interval=config.interval),
                "lightgbm": LightGBMForecaster(interval=config.interval),
            }
            for model_name, model in direct_models.items():
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        forecast = model.forecast(train_months, train_values, 12)
                except ModelFitError as error:
                    raise RuntimeError(
                        f"required forecast model {model_name} failed"
                    ) from error
                target = collected[model_name]
                target["actual"].extend(actual)
                target["predicted"].extend(forecast.predicted)
                target["lower"].extend(forecast.lower)
                target["upper"].extend(forecast.upper)

            series = MonthlySeries(
                "EXPERIMENT", scenario, train_months, train_values
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                selected = forecast_series(series, config)
            if not isinstance(selected, ForecastResult):
                raise AssertionError(
                    "production policy unexpectedly skipped an experiment series"
                )
            if len(selected.points) != 12:
                raise AssertionError(
                    "production policy did not return 12 forecast months"
                )
            expected_targets = tuple(
                add_months(train_months[-1], step).strftime("%Y-%m")
                for step in range(1, 13)
            )
            if tuple(point.target_month for point in selected.points) != expected_targets:
                raise AssertionError("production policy returned unordered target months")
            selections[selected.model] += 1
            target = collected["production_policy"]
            target["actual"].extend(actual)
            target["predicted"].extend(point.predicted for point in selected.points)
            target["lower"].extend(point.lower for point in selected.points)
            target["upper"].extend(point.upper for point in selected.points)

        model_results = {
            model_name: _forecast_metrics(
                values_by_metric["actual"],
                values_by_metric["predicted"],
                values_by_metric["lower"],
                values_by_metric["upper"],
            )
            for model_name, values_by_metric in collected.items()
        }
        scenario_results[scenario] = {
            "models": model_results,
            "production_policy_selections": dict(sorted(selections.items())),
        }
    return {
        "configuration": {
            "months": 84,
            "origins": list(origins),
            "horizon_months": 12,
            "interval": config.interval,
        },
        "scenarios": scenario_results,
    }
