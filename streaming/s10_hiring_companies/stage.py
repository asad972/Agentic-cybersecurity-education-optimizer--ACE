"""S10: estimate daily and rolling distinct employers through the shared stage base."""

from typing import Any

from ace.stage import Window, WindowedStage

from hyperloglog import HiringCompaniesCounter, HyperLogLog


class HiringCompaniesStage(WindowedStage):
    """Estimate distinct hiring companies for each country and event-time day."""

    name = "s10-hiring-companies"
    input = "jobs.clean"
    output = "agg.companies_daily"
    time_field = "posted_date"

    def __init__(self, precision: int = 12) -> None:
        # Retain the sketch size so each daily accumulator and rolling counter match.
        self.precision = precision
        # Validate precision now rather than during job processing.
        HiringCompaniesCounter(precision=precision)

    def new(self) -> dict[str, Any]:
        # Each country/day window tracks distinct companies and missing-name ads.
        return {"companies": HyperLogLog(precision=self.precision), "missing_company": 0}

    def add(self, state: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        # A missing or blank company is counted separately and not hashed.
        company = record.get("company")
        if company is None or (isinstance(company, str) and not company.strip()):
            state["missing_company"] += 1
            return state
        if not isinstance(company, str):
            raise ValueError("jobs.clean company must be text when present")

        # Normalize and add the company name to the current day's sketch.
        state["companies"].add(company)
        return state

    def result(
        self, key: str, window: Window, state: dict[str, Any], memory: dict
    ) -> list[dict[str, Any]]:
        # Keep daily sketches in the checkpointed per-country memory for 30-day merges.
        counter = memory.setdefault(
            "hiring_companies", HiringCompaniesCounter(precision=self.precision)
        )
        if not isinstance(counter, HiringCompaniesCounter):
            raise TypeError("S10 checkpoint memory has an unexpected value")

        # Save this day's sketch and count before making the rolling estimate.
        counter.add_daily_sketch(
            key,
            window.start.date(),
            state["companies"],
            state["missing_company"],
        )
        return [counter.emit(key, window.start.date())]
