"""Daily-to-monthly and country-to-EU series construction."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable, Mapping

from .types import DemandPoint, MonthlySeries


def month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def add_months(value: date, count: int) -> date:
    index = value.year * 12 + value.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


def complete_month_cutoff(as_of: date) -> date:
    """The first day of the current, incomplete month."""
    return month_start(as_of)


def monthly_series(
    region: str,
    skill: str,
    daily: Iterable[DemandPoint],
    as_of: date,
) -> MonthlySeries | None:
    cutoff = complete_month_cutoff(as_of)
    totals: dict[date, float] = defaultdict(float)
    for point in daily:
        if point.count < 0:
            raise ValueError("Daily Demand count must be nonnegative")
        if point.day < cutoff:
            totals[month_start(point.day)] += point.count
    if not totals:
        return None
    first = min(totals)
    last = add_months(cutoff, -1)
    months: list[date] = []
    values: list[float] = []
    current = first
    while current <= last:
        months.append(current)
        values.append(totals.get(current, 0.0))
        current = add_months(current, 1)
    return MonthlySeries(region, skill, tuple(months), tuple(values))


def eu_series(
    skill: str,
    countries: Iterable[str],
    country_series: Mapping[str, MonthlySeries],
) -> MonthlySeries | None:
    """Sum the fixed country scope; countries without history contribute zero."""
    selected = [country_series[country] for country in countries if country in country_series]
    if not selected:
        return None
    first = min(series.months[0] for series in selected)
    last = max(series.months[-1] for series in selected)
    lookup = {
        country: dict(zip(series.months, series.values, strict=True))
        for country, series in country_series.items()
        if country in countries
    }
    months: list[date] = []
    values: list[float] = []
    current = first
    while current <= last:
        months.append(current)
        values.append(sum(by_month.get(current, 0.0) for by_month in lookup.values()))
        current = add_months(current, 1)
    return MonthlySeries("EU", skill, tuple(months), tuple(values))
