"""Bounded-memory Lossy Counting and 30-day aggregation for emerging skills."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil


@dataclass(frozen=True)
class Estimate:
    """Approximate frequency and its maximum possible undercount."""

    count: int
    max_error: int


class LossyCounting:
    """A single-stream summary that bounds memory with an error parameter."""

    def __init__(self, epsilon: float = 0.01) -> None:
        if not 0 < epsilon < 1:
            raise ValueError("epsilon must be greater than 0 and less than 1")
        self.epsilon = epsilon
        self.width = ceil(1 / epsilon)
        self.total = 0
        # Each entry stores [estimated count, maximum undercount].
        self._items: dict[str, list[int]] = {}

    def add(self, item: str) -> None:
        normalized = item.strip().lower()
        if not normalized:
            raise ValueError("item must not be empty")

        self.total += 1
        bucket = ceil(self.total / self.width)
        if normalized in self._items:
            self._items[normalized][0] += 1
        else:
            self._items[normalized] = [1, bucket - 1]

        # At each bucket boundary, discard entries that cannot be frequent.
        if self.total % self.width == 0:
            self._items = {
                key: value
                for key, value in self._items.items()
                if value[0] + value[1] > bucket
            }

    def estimates(self) -> dict[str, Estimate]:
        return {
            item: Estimate(count=count, max_error=error)
            for item, (count, error) in self._items.items()
        }

    @property
    def max_untracked_count(self) -> int:
        """Bound how many times a pruned item could have appeared."""
        return int(self.epsilon * self.total)


class EmergingSkillsCounter:
    """Keep one daily sketch per country and produce schema-shaped output."""

    def __init__(self, epsilon: float = 0.01, limit: int = 50) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.epsilon = epsilon
        self.limit = limit
        self._daily: dict[str, dict[date, LossyCounting]] = defaultdict(dict)

    def add(self, country: str, day: date | str, mention: str) -> None:
        if len(country) != 2 or not country.isalpha() or country != country.upper():
            raise ValueError("country must be an uppercase ISO alpha-2 code")
        day = self._parse_day(day)
        counter = self._daily[country].setdefault(
            day, LossyCounting(epsilon=self.epsilon)
        )
        counter.add(mention)

    def emit(self, country: str, day: date | str) -> dict[str, object]:
        if len(country) != 2 or not country.isalpha() or country != country.upper():
            raise ValueError("country must be an uppercase ISO alpha-2 code")
        end = self._parse_day(day)
        start = end - timedelta(days=29)
        daily = self._daily.get(country, {})
        for old_day in [item_day for item_day in daily if item_day < start]:
            del daily[old_day]

        # Sketches are per-day, so add their estimates and error bounds.
        summaries = [
            counter.estimates()
            for item_day, counter in daily.items()
            if start <= item_day <= end
        ]
        mentions = {mention for summary in summaries for mention in summary}
        totals: dict[str, Estimate] = {}
        counters = [
            counter
            for item_day, counter in daily.items()
            if start <= item_day <= end
        ]
        for mention in mentions:
            count = 0
            max_error = 0
            for counter, summary in zip(counters, summaries):
                estimate = summary.get(mention)
                if estimate is None:
                    # The item may have been pruned from this day's summary.
                    max_error += counter.max_untracked_count
                else:
                    count += estimate.count
                    max_error += estimate.max_error
            totals[mention] = Estimate(count=count, max_error=max_error)

        items = [
            {
                "mention": mention,
                "count": estimate.count,
                "max_error": estimate.max_error,
            }
            for mention, estimate in sorted(
                totals.items(), key=lambda entry: (-entry[1].count, entry[0])
            )[: self.limit]
        ]
        return {"country": country, "day": end.isoformat(), "items": items}

    @staticmethod
    def _parse_day(day: date | str) -> date:
        if isinstance(day, date):
            return day
        try:
            return date.fromisoformat(day)
        except ValueError as error:
            raise ValueError("day must be an ISO 8601 date") from error
