"""Synthetic hourly aggregates; no collector, S7 checkout or API keys needed."""

from datetime import datetime, timedelta, timezone


def hourly_fixture():
    """Seven days for HU/DE; HU has one phishing spike at hour 132."""
    start = datetime(2026, 10, 12, tzinfo=timezone.utc)
    for hour in range(168):
        moment = start + timedelta(hours=hour)
        for index, country in enumerate(("HU", "DE")):
            counts = {"malware": 3 + (hour + index) % 3,
                      "phishing": 2 + (hour + index) % 2,
                      "scanning": 2 + index % 3}
            if country == "HU" and hour == 132:
                counts["phishing"] = 180
            yield {"country": country, "day": moment.date().isoformat(),
                   "hour": moment.hour, "counts": counts}
