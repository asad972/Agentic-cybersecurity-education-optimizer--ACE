from datetime import date

import pytest

from ace_gap.__main__ import parser
from ace_gap.month import TargetMonth, previous_complete_month


def test_cli_accepts_optional_month():
    assert parser().parse_args(["--month", "2026-10"]).month == TargetMonth(2026, 10)
    assert parser().parse_args([]).month is None


def test_cli_default_helper_uses_previous_utc_month():
    assert previous_complete_month(date(2026, 11, 11)) == TargetMonth(2026, 10)


def test_cli_rejects_invalid_month():
    with pytest.raises(SystemExit):
        parser().parse_args(["--month", "October"])
