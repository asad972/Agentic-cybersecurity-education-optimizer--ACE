"""Exact threat-type counts per Country and UTC tumbling window."""

from collections import Counter
from datetime import timedelta

from ace import contracts
from ace.stage import Window, WindowedStage


class ThreatFrequencyStage(WindowedStage):
    """One S7 branch; instantiate once for hourly and once for daily output."""

    time_field = "observed_at"
    grace = timedelta(minutes=15)

    def __init__(self, period: str = "hourly"):
        if period not in {"hourly", "daily"}:
            raise ValueError("period must be 'hourly' or 'daily'")
        self.period = period
        self.name = f"s7-threat-frequency-{period}"
        self.input = contracts.topic("threats.events").name
        self.output = contracts.topic(f"agg.threats_{period}").name
        self.window = timedelta(hours=1) if period == "hourly" else timedelta(days=1)

    def new(self):
        # Exact replay protection costs O(distinct IDs per Country/window),
        # including checkpoint serialization. S1 deduplicates jobs, not threats.
        # Both fields are picklable and belong to this Country/window only.
        return {"counts": Counter(), "seen": set()}

    def add(self, state, record):
        identity = record["event_id"]
        if identity not in state["seen"]:
            state["seen"].add(identity)
            state["counts"][record["threat_type"]] += 1
        return state

    def result(self, key: str, window: Window, state, memory):
        row = {
            "country": key,
            "day": window.day,
            "counts": dict(sorted(state["counts"].items())),
        }
        if self.period == "hourly":
            row["hour"] = window.start.hour
        return [row]
