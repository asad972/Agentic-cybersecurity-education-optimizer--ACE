from datetime import date

import pytest

from ace_forecast.series import add_months, eu_series, monthly_series
from ace_forecast.types import DemandPoint, MonthlySeries


def test_daily_counts_become_zero_filled_complete_months():
    result = monthly_series(
        "DE",
        "Cloud Security",
        [
            DemandPoint(date(2026, 1, 1), 2),
            DemandPoint(date(2026, 1, 31), 3),
            DemandPoint(date(2026, 3, 4), 7),
            DemandPoint(date(2026, 4, 1), 99),
        ],
        date(2026, 4, 15),
    )
    assert result is not None
    assert result.months == (date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1))
    assert result.values == (5.0, 0.0, 7.0)


def test_current_month_only_is_empty_and_negative_counts_fail():
    assert monthly_series("DE", "Linux", [DemandPoint(date(2026, 4, 1), 2)], date(2026, 4, 3)) is None
    with pytest.raises(ValueError):
        monthly_series("DE", "Linux", [DemandPoint(date(2026, 1, 1), -1)], date(2026, 4, 3))


def test_eu_sums_fixed_countries_and_zero_fills_absent_history():
    de = MonthlySeries("DE", "Linux", (date(2026, 1, 1), date(2026, 2, 1)), (3.0, 4.0))
    fr = MonthlySeries("FR", "Linux", (date(2026, 2, 1), date(2026, 3, 1)), (5.0, 6.0))
    us = MonthlySeries("US", "Linux", (date(2026, 1, 1),), (100.0,))
    result = eu_series("Linux", ("DE", "FR"), {"DE": de, "FR": fr, "US": us})
    assert result is not None
    assert result.region == "EU"
    assert result.values == (3.0, 9.0, 6.0)


def test_month_addition_crosses_years():
    assert add_months(date(2026, 12, 1), 2) == date(2027, 2, 1)
