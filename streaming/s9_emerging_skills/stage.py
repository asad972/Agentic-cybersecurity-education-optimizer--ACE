"""Adapt skills.demand messages to a stateful S9 daily aggregator."""

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from lossy_counting import EmergingSkillsCounter


class EmergingSkillsStage:
    """Keep S9 sketches across daily calls and emit closed-day results."""

    def __init__(self, epsilon: float = 0.01, limit: int = 50) -> None:
        self._counter = EmergingSkillsCounter(epsilon=epsilon, limit=limit)

    def process_day(
        self,
        messages: Iterable[Mapping[str, Any]],
        day: date | str,
    ) -> list[dict[str, object]]:
        """Ingest one day's skills.demand records and emit each active country.

        Keep this stage instance alive between days so the 30-day summaries
        remain available. A production Flink operator must restore this state
        from its checkpoint after restart.
        """
        target_day = date.fromisoformat(day) if isinstance(day, str) else day
        countries: set[str] = set()
        for message in messages:
            country = message.get("country")
            event_day = message.get("posted_date")
            mentions = message.get("unmapped", [])
            if not isinstance(country, str):
                raise ValueError("skills.demand country must be text")
            if event_day != target_day.isoformat():
                continue
            if not isinstance(mentions, list):
                raise ValueError("skills.demand unmapped must be a list")
            countries.add(country)
            for mention in mentions:
                if not isinstance(mention, str):
                    raise ValueError("each unmapped mention must be text")
                self._counter.add(country, target_day, mention)

        return [
            self._counter.emit(country, target_day) for country in sorted(countries)
        ]
