"""S5: rolling 365-day profile Supply per country and canonical Skill."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from ace.stage import Window, WindowedStage

from .exponential_histogram import SupplyCounter

LOGGER = logging.getLogger(__name__)


class SupplyFrequency(WindowedStage):
    """Approximate profiles containing each Skill with DGIM histograms."""

    name = "s5-supply-frequency"
    input = "skills.supply"
    output = "agg.supply_daily"
    time_field = "profile_date"

    def __init__(self, window_days: int = 365, epsilon: float = 0.05) -> None:
        self.window_days = window_days
        self.epsilon = epsilon
        # Validate configuration when the job is built, not after records arrive.
        SupplyCounter(window_days=window_days, epsilon=epsilon)

    def new(self) -> Counter[str]:
        """Create one exact daily accumulator for a country window."""
        return Counter()

    def add(
        self, state: Counter[str], record: dict[str, Any]
    ) -> Counter[str]:
        """Count one profile at most once for each exact canonical Skill."""
        skills = record["skills"]
        for skill in set(skills):
            if skill.strip():
                state[skill] += 1
        return state

    def result(
        self, key: str, window: Window, state: Counter[str], memory: dict
    ) -> list[dict[str, object]]:
        """Add the closed day and emit its rolling Supply snapshot."""
        counter = memory.setdefault(
            "supply_frequency",
            SupplyCounter(window_days=self.window_days, epsilon=self.epsilon),
        )
        if not isinstance(counter, SupplyCounter):
            raise TypeError("S5 checkpoint memory has an unexpected value")

        day = window.start.date()
        if counter.last_day is not None and day <= counter.last_day:
            category = "duplicate" if day == counter.last_day else "stale"
            LOGGER.warning(
                "Skipping %s S5 window country=%s day=%s last_day=%s",
                category,
                key,
                day.isoformat(),
                counter.last_day.isoformat(),
            )
            return []
        counter.add_daily_counts(day, state)
        return [counter.emit(key, day)]
