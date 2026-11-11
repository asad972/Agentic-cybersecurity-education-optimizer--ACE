from datetime import date

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
