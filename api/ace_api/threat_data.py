"""M4 threat landscape reads (#40): hourly threat counts and S8 bursts for one country.

S7 writes one `threats_by_hour` partition per country and day (hour, threat type,
count) and S8 writes its bursts to `alerts`. Every query has a complete partition
key, a row limit and a timeout, like the Europe map reads in map_data.py.
"""
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import logging

from cassandra import DriverException
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

log = logging.getLogger(__name__)

MAX_DAYS = 31
# One day of one country: 24 hours times the threat types seen. The threat agent
# knows a few dozen types, so a real partition stays far below this.
HOUR_ROWS = 2400
# Alerts of one country and day, drift (S6) and bursts (S8) together.
ALERT_ROWS = 200
NOTE = ("Feed observations by source geography, in UTC hours; these are not verified attacks or victim counts. "
        "Hours without stored observations are left out, which is not the same as zero.")


class HourCount(BaseModel):
    hour: datetime          # start of the UTC hour
    total: int
    counts: dict[str, int]  # threat type -> observations


class Burst(BaseModel):
    created_at: datetime    # the affected UTC hour, as S8 sets it
    threat_type: str
    score: FiniteFloat
    details: str


class HourlyThreats(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    country: str
    first: date = Field(serialization_alias="from")
    last: date = Field(serialization_alias="to")
    total: int
    by_type: dict[str, int]  # totals over the period, largest first
    hours: list[HourCount]
    bursts: list[Burst]
    bursts_truncated: bool   # a day had more than ALERT_ROWS alerts; the oldest are not shown
    note: str


class ThreatReader:
    def __init__(self, session):
        self.session = session

    def _read(self, query, parameters, limit):
        try:
            return list(self.session.execute(query + " LIMIT %s", (*parameters, limit + 1), timeout=5))
        except (DriverException, RuntimeError) as error:
            log.warning("Threat read failed (%s)", type(error).__name__)
            raise HTTPException(503, "Threat storage is unavailable") from error

    def hourly(self, country: str, first: date, last: date) -> HourlyThreats:
        hours: dict[datetime, Counter] = {}
        bursts, truncated = [], False
        for offset in range((last - first).days + 1):
            day = first + timedelta(days=offset)
            rows = self._read("SELECT hour, threat_type, count FROM threats_by_hour "
                              "WHERE country = %s AND day = %s", (country, day), HOUR_ROWS)
            if len(rows) > HOUR_ROWS:
                raise HTTPException(503, "An hourly partition exceeds the read limit; refusing to show incomplete counts")
            for row in rows:
                if row.hour is None or not 0 <= row.hour <= 23 or row.count is None:
                    continue
                start = datetime.combine(day, time(row.hour), timezone.utc)
                hours.setdefault(start, Counter())[row.threat_type] += row.count
            alerts = self._read("SELECT created_at, alert_type, subject, score, details FROM alerts "
                                "WHERE country = %s AND day = %s", (country, day), ALERT_ROWS)
            truncated = truncated or len(alerts) > ALERT_ROWS
            for row in alerts[:ALERT_ROWS]:
                if row.alert_type != "threat_burst" or row.score is None:
                    continue
                created = row.created_at
                if created.tzinfo is None:  # the driver returns UTC timestamps without a zone
                    created = created.replace(tzinfo=timezone.utc)
                bursts.append(Burst(created_at=created.astimezone(timezone.utc), threat_type=row.subject,
                                    score=row.score, details=row.details or ""))
        by_type = Counter()
        for counts in hours.values():
            by_type.update(counts)
        return HourlyThreats(
            country=country, first=first, last=last, total=sum(by_type.values()),
            by_type=dict(sorted(by_type.items(), key=lambda item: (-item[1], item[0]))),
            hours=[HourCount(hour=start, total=sum(counts.values()), counts=dict(sorted(counts.items())))
                   for start, counts in sorted(hours.items())],
            bursts=sorted(bursts, key=lambda burst: (burst.created_at, burst.threat_type)),
            bursts_truncated=truncated, note=NOTE)
