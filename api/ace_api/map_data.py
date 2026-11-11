"""M4 Europe-map reads using the existing Cassandra connection and schema.

Every query has a complete partition key and a row limit. This module neither
opens its own connection nor changes the session's row factory.
"""
from collections import Counter
from datetime import date, datetime, timezone
import logging
from typing import Literal

from cassandra import DriverException
from fastapi import HTTPException
from pydantic import BaseModel, FiniteFloat

log = logging.getLogger(__name__)
Metric = Literal["job_ads", "demand", "supply", "gap", "programmes", "companies", "threats"]
COUNTRIES = dict(sorted({
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia",
    "CY": "Cyprus", "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia",
    "FI": "Finland", "FR": "France", "DE": "Germany", "GR": "Greece",
    "HU": "Hungary", "IE": "Ireland", "IT": "Italy", "LV": "Latvia",
    "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands",
    "PL": "Poland", "PT": "Portugal", "RO": "Romania", "SK": "Slovakia",
    "SI": "Slovenia", "ES": "Spain", "SE": "Sweden", "IS": "Iceland",
    "LI": "Liechtenstein", "NO": "Norway", "GB": "United Kingdom", "CH": "Switzerland",
}.items(), key=lambda item: item[1]))
METRICS = {
    "job_ads": ("Job advertisements", "job ads", "daily",
                "Deduplicated job advertisements on the selected UTC date."),
    "demand": ("Skill demand", "job ads mentioning skill", "daily",
               "Daily estimated demand for the exact selected skill."),
    "supply": ("Skill supply", "profiles", "365-day stock snapshot",
               "365-day profile stock as of the selected date. S5 publishes on active dates only; "
               "a missing snapshot is not zero and is not carried forward."),
    "gap": ("Skills gap", "demand / supply", "monthly",
            "Monthly demand/supply ratio. No value also includes insufficient supply; ratios are not summed."),
    "programmes": ("Education programmes", "programmes", "current snapshot",
                   "Currently stored programmes, not historical coverage for the selected date. "
                   "No stored rows does not establish zero programmes."),
    "companies": ("Hiring companies", "estimated distinct companies", "30 days",
                  "S10 estimate of distinct hiring companies in the 30 days ending on the selected date."),
    "threats": ("Threat observations", "feed observations", "daily",
                "Feed observations by source geography; these are not verified attacks or victim counts."),
}


class MapValue(BaseModel):
    country: str
    value: FiniteFloat | None


class MapResponse(BaseModel):
    metric: Metric
    date: date
    item: str | None
    unit: str
    period: str
    note: str
    data: list[MapValue]


class MapReader:
    def __init__(self, session, row_limit=10000):
        self.session = session
        self.row_limit = row_limit

    def read(self, query, parameters, *, limit=None):
        limit = self.row_limit if limit is None else limit
        try:
            rows = list(self.session.execute(query + " LIMIT %s", (*parameters, limit + 1), timeout=5))
        except (DriverException, RuntimeError) as error:
            log.warning("Europe map read failed (%s)", type(error).__name__)
            raise HTTPException(503, "Map storage is unavailable") from error
        if len(rows) > limit:
            raise HTTPException(503, "Map partition exceeds the read limit; refusing to show incomplete counts")
        return rows

    def values(self, metric: Metric, day: date, item: str | None):
        if metric in {"demand", "supply"}:
            # Table comes from the validated enum, never from untrusted SQL.
            rows = self.read(f"SELECT country, count FROM {metric}_map "
                             "WHERE kind = %s AND item = %s AND day = %s", ("skill", item, day))
            return {row.country: row.count for row in rows}
        if metric == "job_ads":
            rows = self.read("SELECT country, count FROM job_ads_by_day WHERE day = %s", (day,))
            return {row.country: row.count for row in rows}
        if metric == "companies":
            rows = self.read("SELECT country, companies_30d FROM companies_by_day WHERE day = %s", (day,))
            return {row.country: row.companies_30d for row in rows}
        if metric == "threats":
            rows = self.read("SELECT country, threat_type, count FROM threats_by_day WHERE day = %s", (day,))
            totals = Counter()
            for row in rows:
                totals[row.country] += row.count
            return dict(totals)
        values = {}
        for country in COUNTRIES:
            if metric == "gap":
                rows = self.read("SELECT gap_ratio FROM skills_gap "
                                 "WHERE country = %s AND month = %s AND skill = %s",
                                 (country, day.strftime("%Y-%m"), item), limit=1)
                values[country] = rows[0].gap_ratio if rows else None
            elif metric == "programmes":
                rows = self.read("SELECT programme_id FROM education_programmes WHERE country = %s", (country,))
                values[country] = len(rows) if rows else None
            else:
                raise ValueError("Unsupported map metric")
        return values

    def country(self, country: str, day: date):
        def top(kind):
            rows = self.read("SELECT rank, item, count FROM top10 "
                             "WHERE country = %s AND day = %s AND kind = %s",
                             (country, day, kind), limit=10)
            return [{"rank": row.rank, "item": row.item, "count": row.count} for row in rows]

        skills, certifications = top("skill"), top("certification")
        # Clustering order is year DESC. This is explicitly the latest baseline,
        # independent of the selected historical day (as in M5's dashboard).
        try:
            workforce = list(self.session.execute(
                "SELECT year, workforce_size, workforce_gap, source FROM workforce_baseline "
                "WHERE country = %s LIMIT 1", (country,), timeout=5))
            alerts = list(self.session.execute(
                "SELECT created_at, alert_type, subject, score, details FROM alerts "
                "WHERE country = %s AND day = %s LIMIT 51", (country, day), timeout=5))
        except (DriverException, RuntimeError) as error:
            log.warning("Europe country read failed (%s)", type(error).__name__)
            raise HTTPException(503, "Map storage is unavailable") from error
        baseline = None
        if workforce:
            row = workforce[0]
            baseline = {field: getattr(row, field) for field in
                        ("year", "workforce_size", "workforce_gap", "source")}
        result = []
        for row in alerts[:50]:
            created = row.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            result.append({"created_at": created.astimezone(timezone.utc), "alert_type": row.alert_type,
                           "subject": row.subject, "score": row.score, "details": row.details or ""})
        return {"country": country, "date": day, "top_skills": skills,
                "top_certifications": certifications, "workforce": baseline,
                "alerts": result, "alerts_truncated": len(alerts) > 50}
