"""DGIM exponential histograms for rolling profile-supply counts.

The implementation follows Datar, Gionis, Indyk and Motwani, "Maintaining
Stream Statistics over Sliding Windows" (2002):
https://www.cs.cmu.edu/~natassa/courses/15-823/F02/papers/DGIM02.pdf

One histogram represents the positive daily profile count for one canonical
Skill. Dates are the stream timestamps, so a 365-day query retains events from
the query day and the preceding 364 calendar days.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta


def _day(value: date | str) -> date:
    if isinstance(value, datetime):
        raise ValueError("day must be a date without a time")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TypeError("day must be a date or ISO 8601 date string")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("day must be an ISO 8601 date") from error


@dataclass(frozen=True, slots=True)
class Bucket:
    """A power-of-two number of events and its most recent event day."""

    size: int
    end_day: date


class ExponentialHistogram:
    """Approximate a positive count over a bounded calendar-day window."""

    def __init__(self, window_days: int = 365, epsilon: float = 0.05) -> None:
        if isinstance(window_days, bool) or not isinstance(window_days, int):
            raise TypeError("window_days must be an integer")
        if window_days < 1:
            raise ValueError("window_days must be at least 1")
        if isinstance(epsilon, bool) or not isinstance(epsilon, (int, float)):
            raise TypeError("epsilon must be a number")
        epsilon = float(epsilon)
        if not math.isfinite(epsilon) or not 0 < epsilon <= 0.5:
            raise ValueError("epsilon must be finite and in the interval (0, 0.5]")

        self.window_days = window_days
        self.epsilon = epsilon
        self.k = math.ceil(1 / epsilon)
        self.max_buckets_per_size = math.ceil(self.k / 2) + 1
        self._buckets: list[Bucket] = []  # oldest to newest
        self._last_day: date | None = None

    @property
    def buckets(self) -> tuple[Bucket, ...]:
        """Return an immutable snapshot for diagnostics and accuracy tests."""
        return tuple(self._buckets)

    @property
    def bucket_count(self) -> int:
        return len(self._buckets)

    def add(self, day: date | str, value: int = 1) -> None:
        """Add a positive integer count at one nondecreasing event day."""
        event_day = _day(day)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("value must be an integer")
        if value < 1:
            raise ValueError("value must be at least 1")
        self._advance(event_day)

        self._buckets.extend(Bucket(1, event_day) for _ in range(value))
        self._compress()

    def estimate(self, day: date | str) -> int:
        """Estimate the active count for the inclusive window ending on day."""
        query_day = _day(day)
        self._advance(query_day)
        self._expire(query_day)
        if not self._buckets:
            return 0

        total = sum(bucket.size for bucket in self._buckets)
        # DGIM estimates half of the possibly partial oldest bucket. Bucket
        # sizes are powers of two; keeping size-one buckets whole is the upward
        # integer rounding required by the agg_count contract.
        return total - self._buckets[0].size // 2

    def _advance(self, new_day: date) -> None:
        if self._last_day is not None and new_day < self._last_day:
            raise ValueError("event and query days must be nondecreasing")
        self._last_day = new_day

    def _expire(self, query_day: date) -> None:
        first_active = query_day - timedelta(days=self.window_days - 1)
        first_retained = 0
        while (
            first_retained < len(self._buckets)
            and self._buckets[first_retained].end_day < first_active
        ):
            first_retained += 1
        if first_retained:
            del self._buckets[:first_retained]

    def _compress(self) -> None:
        while True:
            counts = Counter(bucket.size for bucket in self._buckets)
            overflowing = [
                size
                for size, count in counts.items()
                if count > self.max_buckets_per_size
            ]
            if not overflowing:
                return

            size = min(overflowing)
            indices = [
                index
                for index, bucket in enumerate(self._buckets)
                if bucket.size == size
            ]
            older, newer = indices[0], indices[1]
            merged = Bucket(size * 2, self._buckets[newer].end_day)
            del self._buckets[newer]
            self._buckets[older] = merged


class SupplyCounter:
    """Maintain one rolling histogram per observed canonical Skill."""

    def __init__(self, window_days: int = 365, epsilon: float = 0.05) -> None:
        # Construct once to apply the histogram's configuration validation.
        ExponentialHistogram(window_days=window_days, epsilon=epsilon)
        self.window_days = window_days
        self.epsilon = float(epsilon)
        self._histograms: dict[str, ExponentialHistogram] = {}
        self._last_day: date | None = None

    @property
    def active_skills(self) -> tuple[str, ...]:
        return tuple(sorted(self._histograms))

    @property
    def bucket_count(self) -> int:
        return sum(histogram.bucket_count for histogram in self._histograms.values())

    def add_daily_counts(
        self, day: date | str, counts: Mapping[str, int]
    ) -> None:
        event_day = _day(day)
        if self._last_day is not None and event_day < self._last_day:
            raise ValueError("daily counts must be added in nondecreasing day order")

        checked: list[tuple[str, int]] = []
        for skill, count in counts.items():
            if not isinstance(skill, str) or not skill:
                raise ValueError("Skill names must be non-empty strings")
            if isinstance(count, bool) or not isinstance(count, int):
                raise TypeError("Skill counts must be integers")
            if count < 1:
                raise ValueError("Skill counts must be positive")
            checked.append((skill, count))

        for skill, count in sorted(checked):
            histogram = self._histograms.setdefault(
                skill,
                ExponentialHistogram(
                    window_days=self.window_days, epsilon=self.epsilon
                ),
            )
            histogram.add(event_day, count)
        self._last_day = event_day

    def emit(self, country: str, day: date | str) -> dict[str, object]:
        if (
            not isinstance(country, str)
            or len(country) != 2
            or not country.isalpha()
            or country != country.upper()
        ):
            raise ValueError("country must be an uppercase ISO alpha-2 code")
        query_day = _day(day)
        if self._last_day is not None and query_day < self._last_day:
            raise ValueError("snapshots must be emitted in nondecreasing day order")

        counts: list[dict[str, object]] = []
        expired: list[str] = []
        for skill, histogram in sorted(self._histograms.items()):
            estimate = histogram.estimate(query_day)
            if estimate:
                counts.append(
                    {"kind": "skill", "item": skill, "count": estimate}
                )
            else:
                expired.append(skill)
        for skill in expired:
            del self._histograms[skill]
        self._last_day = query_day
        return {"country": country, "day": query_day.isoformat(), "counts": counts}
