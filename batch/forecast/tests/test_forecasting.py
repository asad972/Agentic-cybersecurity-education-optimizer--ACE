from datetime import date

import pytest

from ace_forecast.forecasting import evaluate, forecast_series
from ace_forecast.models import ModelFitError
from ace_forecast.series import add_months
from ace_forecast.types import ModelForecast, MonthlySeries, SkippedSeries


class FixedModel:
    def __init__(self, name, evaluation_values, final_values=None, fail=False):
        self.name = name
        self.evaluation_values = evaluation_values
        self.final_values = final_values or evaluation_values
        self.fail = fail

    def forecast(self, months, values, horizon):
        if self.fail:
            raise ModelFitError("expected failure")
        source = self.evaluation_values if horizon == 3 else self.final_values
        predicted = tuple(float(source[index % len(source)]) for index in range(horizon))
        return ModelForecast(self.name, predicted, predicted, predicted)


class RecordingModel(FixedModel):
    def __init__(self, name, values):
        super().__init__(name, values)
        self.history_lengths = []

    def forecast(self, months, values, horizon):
        self.history_lengths.append(len(values))
        return super().forecast(months, values, horizon)


def make_series(length, value=10.0):
    start = date(2024, 1, 1)
    return MonthlySeries(
        "DE",
        "Cloud Security",
        tuple(add_months(start, index) for index in range(length)),
        tuple(value for _ in range(length)),
    )


def test_exact_history_tiers_and_twelve_target_months():
    skipped = forecast_series(make_series(14))
    assert isinstance(skipped, SkippedSeries)
    assert skipped.reason == "fewer_than_15_months"

    fallback = forecast_series(make_series(15))
    assert fallback.model == "seasonal_naive"
    assert len(fallback.points) == 12
    assert fallback.points[0].target_month == "2025-04"

    candidates = {"sarima": FixedModel("sarima", [10, 10, 10]), "lightgbm": FixedModel("lightgbm", [9, 9, 9])}
    advanced = forecast_series(make_series(27), candidates=candidates)
    assert advanced.model == "sarima"


def test_lower_mae_wins_and_sarima_wins_exact_tie():
    series = make_series(27)
    result = forecast_series(series, candidates={
        "sarima": FixedModel("sarima", [8, 8, 8]),
        "lightgbm": FixedModel("lightgbm", [10, 10, 10]),
    })
    assert result.model == "lightgbm"
    tied = forecast_series(series, candidates={
        "sarima": FixedModel("sarima", [9, 9, 9]),
        "lightgbm": FixedModel("lightgbm", [9, 9, 9]),
    })
    assert tied.model == "sarima"


def test_final_three_months_are_held_out_then_winner_refits_full_history():
    sarima = RecordingModel("sarima", [10, 10, 10])
    lightgbm = RecordingModel("lightgbm", [8, 8, 8])
    result = forecast_series(make_series(27), candidates={"sarima": sarima, "lightgbm": lightgbm})
    assert result.model == "sarima"
    assert sarima.history_lengths == [24, 27]
    assert lightgbm.history_lengths == [24]


def test_candidate_failure_falls_back_to_other_then_seasonal_naive():
    series = make_series(27)
    other = forecast_series(series, candidates={
        "sarima": FixedModel("sarima", [], fail=True),
        "lightgbm": FixedModel("lightgbm", [10, 10, 10]),
    })
    assert other.model == "lightgbm"
    fallback = forecast_series(series, candidates={
        "sarima": FixedModel("sarima", [], fail=True),
        "lightgbm": FixedModel("lightgbm", [], fail=True),
    })
    assert fallback.model == "seasonal_naive"


def test_mape_ignores_zero_actuals_and_is_null_when_all_are_zero():
    assert evaluate([0, 10, 20], [99, 8, 18]).mape == pytest.approx(15.0)
    assert evaluate([0, 0], [2, 3]).mape is None


def test_final_forecasts_are_nonnegative_and_bands_coherent():
    result = forecast_series(make_series(15, 0.0))
    assert all(0 <= point.lower <= point.predicted <= point.upper for point in result.points)
