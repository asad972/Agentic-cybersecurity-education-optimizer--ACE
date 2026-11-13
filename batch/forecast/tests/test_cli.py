from datetime import date

import pytest

from ace_forecast.__main__ import parser


def test_cli_accepts_optional_as_of():
    assert parser().parse_args(["--as-of", "2026-11-01"]).as_of == date(2026, 11, 1)
    assert parser().parse_args([]).as_of is None


def test_cli_rejects_invalid_date():
    with pytest.raises(SystemExit):
        parser().parse_args(["--as-of", "01-11-2026"])
