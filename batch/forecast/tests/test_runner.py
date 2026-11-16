from datetime import date, datetime, timezone

import pytest

from ace_forecast.config import SKILLS_SCOPE
from ace_forecast.runner import run_forecasts
from ace_forecast.series import add_months
from ace_forecast.types import DemandPoint, ForecastPoint, ForecastResult, SkippedSeries


class MemoryRepository:
    def __init__(self, data=None, fail_write=False):
        self.data = data or {}
        self.reads = []
        self.rows = []
        self.fail_write = fail_write

    def read_daily(self, country, skill):
        self.reads.append((country, skill))
        return tuple(self.data.get((country, skill), ()))

    def write(self, rows):
        if self.fail_write:
            raise RuntimeError("write failed")
        self.rows.extend(rows)

    def close(self):
        pass


def monthly_points(start, values):
    return tuple(DemandPoint(add_months(start, index), value) for index, value in enumerate(values))


def deterministic_forecast(series, config):
    if len(series.values) < 15:
        return SkippedSeries(series.region, series.skill, len(series.values), "short")
    points = tuple(ForecastPoint(f"2027-{month:02d}", 1, 0, 2) for month in range(1, 13))
    return ForecastResult(series.region, series.skill, "sarima", len(series.values), None, points)


def test_runner_isolates_countries_builds_eu_and_shares_trained_at():
    start = date(2025, 1, 1)
    data = {
        ("DE", "Linux"): monthly_points(start, [1] * 15),
        ("FR", "Linux"): monthly_points(start, [2] * 15),
    }
    repository = MemoryRepository(data)
    trained_at = datetime(2026, 11, 11, tzinfo=timezone.utc)
    summary = run_forecasts(
        repository,
        ["Linux"],
        date(2026, 5, 1),
        trained_at=trained_at,
        forecast=deterministic_forecast,
    )
    assert repository.reads == [(country, "Linux") for country in SKILLS_SCOPE]
    assert summary.forecast_series == 3
    assert summary.rows_written == 36
    assert {row.region for row in repository.rows} == {"DE", "FR", "EU"}
    assert {row.trained_at for row in repository.rows} == {trained_at}


def test_empty_input_is_successful_noop_with_warning(caplog):
    repository = MemoryRepository()
    summary = run_forecasts(repository, ["Linux"], date(2026, 5, 1), forecast=deterministic_forecast)
    assert summary.rows_written == 0
    assert repository.rows == []
    assert "forecast_no_output" in caplog.text


def test_short_series_is_logged_and_not_written(caplog):
    caplog.set_level("INFO", logger="ace_forecast")
    repository = MemoryRepository({("DE", "Linux"): monthly_points(date(2026, 1, 1), [1, 2])})
    summary = run_forecasts(repository, ["Linux"], date(2026, 4, 1), forecast=deterministic_forecast)
    assert summary.skipped_series == 2  # DE and its EU aggregate
    assert summary.rows_written == 0
    assert "forecast_skipped" in caplog.text


def test_write_failures_fail_the_job():
    repository = MemoryRepository(
        {("DE", "Linux"): monthly_points(date(2025, 1, 1), [1] * 15)},
        fail_write=True,
    )
    with pytest.raises(RuntimeError, match="write failed"):
        run_forecasts(repository, ["Linux"], date(2026, 5, 1), forecast=deterministic_forecast)
