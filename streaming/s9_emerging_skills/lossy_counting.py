"""Bounded-memory Lossy Counting and 30-day aggregation for emerging skills."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil


@dataclass(frozen=True)
class Estimate:
    """Approximate frequency and its maximum possible undercount."""

    # Estimated occurrences retained by the sketch.
    count: int
    # Maximum number of occurrences that may be missing from this estimate.
    max_error: int


class LossyCounting:
    """A single-stream summary that bounds memory with an error parameter."""

    def __init__(self, epsilon: float = 0.01) -> None:
        # Reject invalid accuracy settings before allocating any state.
        if not 0 < epsilon < 1:
            raise ValueError("epsilon must be greater than 0 and less than 1")
        # Store the tolerated error fraction for later calculations.
        self.epsilon = epsilon
        # Each bucket contains approximately 1 / epsilon input items.
        self.width = ceil(1 / epsilon)
        # Count how many items this sketch has consumed.
        self.total = 0
        # Each entry stores [estimated count, maximum undercount].
        self._items: dict[str, list[int]] = {}

    def add(self, item: str) -> None:
        # Make casing and surrounding spaces irrelevant to skill identity.
        normalized = item.strip().lower()
        # Empty names cannot be meaningful emerging-skill candidates.
        if not normalized:
            return

        # Advance the stream length before determining this item's bucket.
        self.total += 1
        # Buckets are numbered from one and have the configured fixed width.
        bucket = ceil(self.total / self.width)
        # Existing candidates gain one exact observed occurrence.
        if normalized in self._items:
            self._items[normalized][0] += 1
        else:
            # A new candidate starts at count one with a bucket-based error.
            self._items[normalized] = [1, bucket - 1]

        # At each bucket boundary, discard entries that cannot be frequent.
        if self.total % self.width == 0:
            # Keep only items whose estimated count plus error exceeds the bucket.
            self._items = {
                key: value
                for key, value in self._items.items()
                if value[0] + value[1] > bucket
            }

    def estimates(self) -> dict[str, Estimate]:
        # Expose immutable estimate objects instead of the sketch's mutable lists.
        return {
            item: Estimate(count=count, max_error=error)
            for item, (count, error) in self._items.items()
        }

    @property
    def max_untracked_count(self) -> int:
        """Bound how many times a pruned item could have appeared."""
        # Any candidate removed from the sketch is bounded by epsilon * stream size.
        return int(self.epsilon * self.total)


class EmergingSkillsCounter:
    """Keep one daily sketch per country and produce schema-shaped output."""

    def __init__(self, epsilon: float = 0.01, limit: int = 50) -> None:
        # The schema permits no more than 50 candidates in one output message.
        if limit < 1:
            raise ValueError("limit must be at least 1")
        # Retain the configuration used to create each daily sketch.
        self.epsilon = epsilon
        self.limit = limit
        # Store one Lossy Counting sketch for every country and event day.
        self._daily: dict[str, dict[date, LossyCounting]] = defaultdict(dict)

    def add(self, country: str, day: date | str, mention: str) -> None:
        # Validate the partition key before creating state for it.
        if len(country) != 2 or not country.isalpha() or country != country.upper():
            raise ValueError("country must be an uppercase ISO alpha-2 code")
        # Accept either a date object or the wire-format ISO date string.
        day = self._parse_day(day)
        # Reuse the country's sketch for this day or create it on first sight.
        counter = self._daily[country].setdefault(
            day, LossyCounting(epsilon=self.epsilon)
        )
        # Add this unmapped mention to that day's bounded-memory summary.
        counter.add(mention)

    def add_daily_sketch(
        self, country: str, day: date | str, sketch: LossyCounting
    ) -> None:
        """Store an already-aggregated daily sketch produced by the Flink window."""
        # Validate the country using the same rule as regular ingestion.
        if len(country) != 2 or not country.isalpha() or country != country.upper():
            raise ValueError("country must be an uppercase ISO alpha-2 code")
        # Normalize the day so the dictionary key is always a date object.
        event_day = self._parse_day(day)
        # A replay or checkpoint restore can close a day again; the latest
        # completed window replaces that day's previous checkpointed result.
        self._daily[country][event_day] = sketch

    def emit(self, country: str, day: date | str) -> dict[str, object]:
        # Validate the requested output partition.
        if len(country) != 2 or not country.isalpha() or country != country.upper():
            raise ValueError("country must be an uppercase ISO alpha-2 code")
        # Use the requested day as the end of the inclusive rolling window.
        end = self._parse_day(day)
        # Include the current day plus the previous 29 calendar days.
        start = end - timedelta(days=29)
        # A country without prior input has no sketches yet.
        daily = self._daily.get(country, {})
        # Remove state that cannot contribute to this or any later 30-day output.
        for old_day in [item_day for item_day in daily if item_day < start]:
            del daily[old_day]

        # Sketches are per-day, so add their estimates and error bounds.
        # Keep the selected sketches in date order for matching them below.
        summaries = [
            counter.estimates()
            for item_day, counter in sorted(daily.items())
            if start <= item_day <= end
        ]
        # A candidate is present if at least one day's sketch retained it.
        mentions = {mention for summary in summaries for mention in summary}
        # Build one combined estimate for every retained candidate.
        totals: dict[str, Estimate] = {}
        # Use the same ordered set of daily counters as the summaries list.
        counters = [
            counter
            for item_day, counter in sorted(daily.items())
            if start <= item_day <= end
        ]
        # Combine the retained counts and account for candidates pruned on each day.
        for mention in mentions:
            # Start with no known occurrences or error.
            count = 0
            max_error = 0
            # A mention can be present in some daily summaries and absent from others.
            for counter, summary in zip(counters, summaries):
                estimate = summary.get(mention)
                if estimate is None:
                    # The item may have been pruned from this day's summary.
                    max_error += counter.max_untracked_count
                else:
                    # Add the day's retained estimate and its undercount bound.
                    count += estimate.count
                    max_error += estimate.max_error
            # Keep the accumulated estimate for output sorting.
            totals[mention] = Estimate(count=count, max_error=max_error)

        # Sort by highest estimated frequency, then alphabetically for stable ties.
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
        # Return the exact shape required by the agg.emerging_skills schema.
        return {"country": country, "day": end.isoformat(), "items": items}

    @staticmethod
    def _parse_day(day: date | str) -> date:
        # Date objects already have the desired representation.
        if isinstance(day, date):
            return day
        # Parse a wire-format date and provide a clear error for bad input.
        try:
            return date.fromisoformat(day)
        except ValueError as error:
            raise ValueError("day must be an ISO 8601 date") from error
