"""Adapt jobs.clean messages to a stateful S10 daily aggregator."""

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from hyperloglog import HiringCompaniesCounter


class HiringCompaniesStage:
    """Keep S10 sketches across daily calls and emit closed-day results."""

    def __init__(self, precision: int = 12) -> None:
        self._counter = HiringCompaniesCounter(precision=precision)

    def process_day(
        self,
        messages: Iterable[Mapping[str, Any]],
        day: date | str,
    ) -> list[dict[str, object]]:
        """Ingest one day's jobs.clean records and emit each active country.

        Keep this stage instance alive between days so daily sketches can be
        merged over 30 days. A production Flink operator must restore this
        state from its checkpoint after restart.
        """
        target_day = date.fromisoformat(day) if isinstance(day, str) else day
        countries: set[str] = set()
        for message in messages:
            country = message.get("country")
            event_day = message.get("posted_date")
            if not isinstance(country, str):
                raise ValueError("jobs.clean country must be text")
            if event_day != target_day.isoformat():
                continue
            company = message.get("company")
            if company is not None and not isinstance(company, str):
                raise ValueError("jobs.clean company must be text when present")
            countries.add(country)
            self._counter.add(country, target_day, company)

        return [
            self._counter.emit(country, target_day) for country in sorted(countries)
        ]
