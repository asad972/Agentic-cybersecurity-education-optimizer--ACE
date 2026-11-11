"""Calendar-month parsing and UTC defaults."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
import re


_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass(frozen=True, slots=True)
class TargetMonth:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.year <= 9999 or not 1 <= self.month <= 12:
            raise ValueError("Target month must be a valid YYYY-MM")

    @classmethod
    def parse(cls, value: str) -> "TargetMonth":
        match = _MONTH.fullmatch(value)
        if not match:
            raise ValueError("Target month must use YYYY-MM")
        return cls(int(match.group(1)), int(match.group(2)))

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])

    def days(self) -> tuple[date, ...]:
        return tuple(
            self.first_day + timedelta(days=offset)
            for offset in range((self.last_day - self.first_day).days + 1)
        )


def previous_complete_month(today: date) -> TargetMonth:
    previous_day = today.replace(day=1) - timedelta(days=1)
    return TargetMonth(previous_day.year, previous_day.month)
