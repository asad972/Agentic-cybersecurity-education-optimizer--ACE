from datetime import date

import numpy as np
import pytest

from ace_forecast.models import LightGBMForecaster, ModelFitError, SarimaForecaster, SeasonalNaiveForecaster
from ace_forecast.series import add_months


def history(length=36):
    months = tuple(add_months(date(2023, 1, 1), index) for index in range(length))
    values = tuple(float(20 + (index % 12) * 2 + index / 4) for index in range(length))
    return months, values


@pytest.mark.parametrize("model", [SeasonalNaiveForecaster(), SarimaForecaster(), LightGBMForecaster()])
def test_models_return_twelve_coherent_deterministic_points(model):
    months, values = history()
    try:
        first = model.forecast(months, values, 12)
    except ModelFitError as exc:
        if isinstance(model, LightGBMForecaster) and "unavailable" in str(exc):
            pytest.skip("LightGBM native OpenMP runtime is unavailable")
        raise
    second = model.forecast(months, values, 12)
    assert first == second
    assert len(first.predicted) == 12
    assert all(0 <= low <= middle <= high for low, middle, high in zip(first.lower, first.predicted, first.upper, strict=True))


@pytest.mark.parametrize(
    "model_type",
    [SeasonalNaiveForecaster, SarimaForecaster, LightGBMForecaster],
)
def test_models_validate_interval(model_type):
    assert model_type(interval=0.8).interval == 0.8
    with pytest.raises(ValueError):
        model_type(interval=1.0)


def test_seasonal_naive_uses_configured_residual_quantiles(monkeypatch):
    months, values = history()
    captured = []
    quantile = np.quantile

    def record(values, probabilities):
        captured.append(tuple(probabilities))
        return quantile(values, probabilities)

    monkeypatch.setattr("ace_forecast.models.np.quantile", record)
    SeasonalNaiveForecaster(interval=0.8).forecast(months, values, 1)
    assert captured == [(pytest.approx(0.1), pytest.approx(0.9))]


def test_sarima_uses_configured_alpha(monkeypatch):
    captured = []

    class Result:
        predicted_mean = np.asarray([2.0])

        def conf_int(self, *, alpha):
            captured.append(alpha)
            return np.asarray([[1.0, 3.0]])

    class Fitted:
        def get_forecast(self, *, steps):
            assert steps == 1
            return Result()

    class FakeSarimax:
        def __init__(self, *args, **kwargs):
            pass

        def fit(self, *, disp):
            assert disp is False
            return Fitted()

    monkeypatch.setattr("statsmodels.tsa.statespace.sarimax.SARIMAX", FakeSarimax)
    months, values = history()
    SarimaForecaster(interval=0.8).forecast(months, values, 1)
    assert captured == [pytest.approx(0.2)]


def test_lightgbm_uses_configured_quantiles(monkeypatch):
    captured = []

    class FakeModel:
        def fit(self, features, targets):
            return self

        def predict(self, features):
            return np.asarray([2.0])

    def model(self, objective="regression", alpha=None):
        captured.append((objective, alpha))
        return FakeModel()

    monkeypatch.setattr(LightGBMForecaster, "_model", model)
    months, values = history()
    LightGBMForecaster(interval=0.8).forecast(months, values, 1)
    assert captured == [
        ("regression", None),
        ("quantile", pytest.approx(0.1)),
        ("quantile", pytest.approx(0.9)),
    ]
