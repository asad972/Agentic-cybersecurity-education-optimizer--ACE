from datetime import date

import pytest

from ace_gap.month import TargetMonth, previous_complete_month


def test_explicit_month_and_leap_year_days():
    target = TargetMonth.parse("2024-02")
    assert target.key == "2024-02"
    assert target.first_day == date(2024, 2, 1)
    assert target.last_day == date(2024, 2, 29)
    assert len(target.days()) == 29


def test_previous_complete_month_crosses_year_boundary():
    assert previous_complete_month(date(2026, 1, 15)) == TargetMonth(2025, 12)
    assert previous_complete_month(date(2026, 11, 1)) == TargetMonth(2026, 10)


@pytest.mark.parametrize("value", ["2026-1", "26-01", "2026-00", "2026-13", "anything"])
def test_invalid_month_is_rejected(value):
    with pytest.raises(ValueError):
        TargetMonth.parse(value)
