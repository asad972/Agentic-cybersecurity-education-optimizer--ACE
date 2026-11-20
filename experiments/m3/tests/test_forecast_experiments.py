"""Tests for the forecast experiment worker."""

import pytest

from ace_forecast.models import LightGBMForecaster, ModelFitError
from experiments.m3.common import MODELS
from experiments.m3.forecast_experiments import run_forecast


def _lightgbm_available() -> bool:
    try:
        from lightgbm import LGBMRegressor  # noqa: F401
    except Exception:
        return False
    return True


@pytest.mark.skipif(
    not _lightgbm_available(),
    reason="host LightGBM native OpenMP runtime is unavailable; Docker is canonical",
)
def test_forecast_reduced_run_has_all_models_and_coherent_metrics() -> None:
    result = run_forecast(scenarios=("stable_seasonality",), origins=(48,))
    scenario = result["scenarios"]["stable_seasonality"]

    assert set(scenario["models"]) == set(MODELS)
    assert sum(scenario["production_policy_selections"].values()) == 1
    for metrics in scenario["models"].values():
        assert metrics["predictions"] == 12
        assert metrics["mae"] >= 0
        assert 0 <= metrics["interval_coverage"] <= 1
        assert metrics["mean_interval_width"] >= 0


def test_required_forecast_model_failure_is_not_silently_omitted(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise ModelFitError("unavailable")

    monkeypatch.setattr(LightGBMForecaster, "forecast", fail)
    with pytest.raises(RuntimeError, match="required forecast model lightgbm failed"):
        run_forecast(scenarios=("stable_seasonality",), origins=(48,))
