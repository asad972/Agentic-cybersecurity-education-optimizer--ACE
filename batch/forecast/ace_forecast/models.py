"""Forecast model adapters with a small, testable interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math
from typing import Protocol, Sequence

import numpy as np

from .series import add_months
from .types import ModelForecast


class ModelFitError(RuntimeError):
    """A candidate could not fit or forecast this series."""


class Forecaster(Protocol):
    name: str

    def forecast(self, months: Sequence[date], values: Sequence[float], horizon: int) -> ModelForecast: ...


def _tail_quantiles(interval: float) -> tuple[float, float]:
    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
        raise ValueError("Forecast interval must be a number between zero and one")
    if not math.isfinite(interval) or not 0.0 < interval < 1.0:
        raise ValueError("Forecast interval must be between zero and one")
    lower = (1.0 - float(interval)) / 2.0
    return lower, 1.0 - lower


def _coherent(
    model: str,
    predicted: Sequence[float],
    lower: Sequence[float],
    upper: Sequence[float],
) -> ModelForecast:
    if not (len(predicted) == len(lower) == len(upper)):
        raise ModelFitError("Model returned inconsistent forecast lengths")
    clean_predicted: list[float] = []
    clean_lower: list[float] = []
    clean_upper: list[float] = []
    for middle, low, high in zip(predicted, lower, upper, strict=True):
        if not all(math.isfinite(float(item)) for item in (middle, low, high)):
            raise ModelFitError("Model returned a non-finite forecast")
        middle = max(0.0, float(middle))
        low = max(0.0, min(float(low), middle))
        high = max(middle, float(high))
        clean_predicted.append(middle)
        clean_lower.append(low)
        clean_upper.append(high)
    return ModelForecast(model, tuple(clean_predicted), tuple(clean_lower), tuple(clean_upper))


@dataclass(frozen=True, slots=True)
class SeasonalNaiveForecaster:
    interval: float = 0.95
    name: str = field(default="seasonal_naive", init=False)

    def __post_init__(self) -> None:
        _tail_quantiles(self.interval)

    def forecast(self, months: Sequence[date], values: Sequence[float], horizon: int) -> ModelForecast:
        if len(values) < 12 or horizon < 1:
            raise ModelFitError("Seasonal-naive requires 12 months and a positive horizon")
        predicted = [float(values[len(values) - 12 + (step % 12)]) for step in range(horizon)]
        residuals = np.asarray(
            [float(values[index]) - float(values[index - 12]) for index in range(12, len(values))],
            dtype=float,
        )
        if residuals.size:
            low, high = _tail_quantiles(self.interval)
            low_residual, high_residual = np.quantile(residuals, [low, high])
        else:
            low_residual = high_residual = 0.0
        lower = [value + float(low_residual) for value in predicted]
        upper = [value + float(high_residual) for value in predicted]
        return _coherent(self.name, predicted, lower, upper)


@dataclass(frozen=True, slots=True)
class SarimaForecaster:
    interval: float = 0.95
    name: str = field(default="sarima", init=False)

    def __post_init__(self) -> None:
        _tail_quantiles(self.interval)

    def forecast(self, months: Sequence[date], values: Sequence[float], horizon: int) -> ModelForecast:
        try:
            from statsmodels.tsa.statespace.sarimax import SARIMAX

            fitted = SARIMAX(
                np.asarray(values, dtype=float),
                order=(1, 1, 1),
                seasonal_order=(1, 0, 0, 12),
                enforce_stationarity=False,
                enforce_invertibility=False,
            ).fit(disp=False)
            result = fitted.get_forecast(steps=horizon)
            predicted = np.asarray(result.predicted_mean, dtype=float)
            interval = np.asarray(result.conf_int(alpha=1.0 - self.interval), dtype=float)
            return _coherent(self.name, predicted, interval[:, 0], interval[:, 1])
        except ModelFitError:
            raise
        except Exception as exc:
            raise ModelFitError(f"SARIMA failed: {type(exc).__name__}") from exc


_LAGS = (1, 2, 3, 6, 12)
_ROLLING = (3, 6, 12)


def _feature(history: Sequence[float], target_month: date, time_index: int) -> list[float]:
    if len(history) < 12:
        raise ModelFitError("LightGBM features require 12 preceding months")
    angle = 2.0 * math.pi * (target_month.month - 1) / 12.0
    return (
        [float(history[-lag]) for lag in _LAGS]
        + [float(np.mean(history[-window:])) for window in _ROLLING]
        + [math.sin(angle), math.cos(angle), float(time_index)]
    )


def _training_data(months: Sequence[date], values: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    features: list[list[float]] = []
    targets: list[float] = []
    for index in range(12, len(values)):
        features.append(_feature(values[:index], months[index], index))
        targets.append(float(values[index]))
    if not features:
        raise ModelFitError("LightGBM requires more than 12 months")
    return np.asarray(features, dtype=float), np.asarray(targets, dtype=float)


@dataclass(frozen=True, slots=True)
class LightGBMForecaster:
    interval: float = 0.95
    random_state: int = 1729
    name: str = field(default="lightgbm", init=False)

    def __post_init__(self) -> None:
        _tail_quantiles(self.interval)

    def _model(self, objective: str = "regression", alpha: float | None = None):
        try:
            from lightgbm import LGBMRegressor
        except Exception as exc:
            raise ModelFitError("LightGBM is unavailable") from exc
        options: dict[str, object] = {
            "objective": objective,
            "n_estimators": 150,
            "learning_rate": 0.03,
            "num_leaves": 15,
            "min_child_samples": 1,
            "random_state": self.random_state,
            "n_jobs": 1,
            "verbosity": -1,
            "deterministic": True,
            "force_col_wise": True,
        }
        if alpha is not None:
            options["alpha"] = alpha
        return LGBMRegressor(**options)

    def forecast(self, months: Sequence[date], values: Sequence[float], horizon: int) -> ModelForecast:
        try:
            features, targets = _training_data(months, values)
            lower_quantile, upper_quantile = _tail_quantiles(self.interval)
            point_model = self._model()
            lower_model = self._model("quantile", lower_quantile)
            upper_model = self._model("quantile", upper_quantile)
            for model in (point_model, lower_model, upper_model):
                model.fit(features, targets)

            histories = [list(map(float, values)) for _ in range(3)]
            predictions: list[list[float]] = [[], [], []]
            models = (point_model, lower_model, upper_model)
            for step in range(1, horizon + 1):
                target_month = add_months(months[-1], step)
                for index, model in enumerate(models):
                    row = np.asarray(
                        [_feature(histories[index], target_month, len(histories[index]))],
                        dtype=float,
                    )
                    value = float(model.predict(row)[0])
                    predictions[index].append(value)
                    histories[index].append(value)
            return _coherent(self.name, predictions[0], predictions[1], predictions[2])
        except ModelFitError:
            raise
        except Exception as exc:
            raise ModelFitError(f"LightGBM failed: {type(exc).__name__}") from exc
