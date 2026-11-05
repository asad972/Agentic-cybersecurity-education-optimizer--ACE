"""Deterministic, entirely synthetic observations: no IP addresses or API keys."""
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from .utils import utc_text

COUNTRIES = ("HU", "DE", "FR", "GB", "US", "JP", "BR", "ZA")


def fixture_events(hours: int = 168):
    if not 1 <= hours <= 24 * 31:
        raise ValueError("fixture hours must be between 1 and 744")
    start = datetime(2026, 10, 12, tzinfo=timezone.utc)
    rows = []
    for hour in range(hours):
        for index, country in enumerate(COUNTRIES):
            # A deliberately missing JP hour is missing coverage, never zero.
            if country == "JP" and hour == 80:
                continue
            counts = {"malware": 3 + (hour + index) % 3,
                      "phishing": 2 + (hour + index) % 2,
                      "scanning": 2 + index % 3}
            if country == "HU" and hour == 132:
                counts["phishing"] = 180
            for kind, count in counts.items():
                for sequence in range(count):
                    timestamp = start + timedelta(hours=hour, seconds=sequence * 10 + index)
                    identity = f"ace-fixture-v1:{country}:{hour}:{kind}:{sequence}"
                    rows.append({"event_id": str(uuid5(NAMESPACE_URL, identity)), "source": "fixture",
                        "country": country, "observed_at": utc_text(timestamp), "threat_type": kind,
                        "severity": 7 if count >= 100 else 3})
    return sorted(rows, key=lambda row: (row["observed_at"], row["event_id"]))
