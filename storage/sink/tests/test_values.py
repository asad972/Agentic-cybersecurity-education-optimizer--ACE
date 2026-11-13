from datetime import date, datetime, timezone

import pytest

from ace_sink.rows import Unstorable
from ace_sink.values import day, month, timestamp


def test_day_and_month():
    assert day("2026-10-01") == date(2026, 10, 1)
    assert month("2026-10-01") == "2026-10"


def test_time_is_stored_in_utc():
    assert timestamp("2026-10-01T23:59:00+02:00") == datetime(2026, 10, 1, 21, 59, tzinfo=timezone.utc)


def test_time_without_a_zone_is_utc():
    assert timestamp("2026-10-01T12:00:00") == datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def test_lower_case_t_and_z_are_read():
    assert timestamp("2026-10-01t12:00:00z") == datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("read, text", [
    (day, "2026-02-30"),
    (month, "October 2026"),
    (timestamp, "2026-10-01T25:00:00Z"),
])
def test_value_python_cannot_read_is_unstorable(read, text):
    with pytest.raises(Unstorable, match="cannot read"):
        read(text)
