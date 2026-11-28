"""Cassandra boundary for Demand input and forecast output."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from ace.batch import CassandraRepositoryBase

from .types import DemandPoint, ForecastRow


SELECT_DEMAND = """
SELECT day, count
FROM demand_trend
WHERE country = ? AND kind = ? AND item = ?
""".strip()

UPSERT_FORECAST = """
INSERT INTO forecasts
  (region, skill, target_month, predicted, lower, upper, model, trained_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
""".strip()


class ForecastRepository(Protocol):
    def read_daily(self, country: str, skill: str) -> tuple[DemandPoint, ...]: ...
    def write(self, rows: Sequence[ForecastRow]) -> None: ...
    def close(self) -> None: ...


class CassandraForecastRepository(CassandraRepositoryBase):
    def __init__(self, session, cluster=None):
        super().__init__(session, cluster)
        self._select = session.prepare(SELECT_DEMAND)
        self._upsert = session.prepare(UPSERT_FORECAST)

    def read_daily(self, country: str, skill: str) -> tuple[DemandPoint, ...]:
        rows = self._session.execute(self._select, (country, "skill", skill))
        result = tuple(DemandPoint(_as_date(row.day), int(row.count)) for row in rows)
        return tuple(sorted(result, key=lambda point: point.day))

    def write(self, rows: Sequence[ForecastRow]) -> None:
        for row in rows:
            self._session.execute(
                self._upsert,
                (
                    row.region,
                    row.skill,
                    row.target_month,
                    row.predicted,
                    row.lower,
                    row.upper,
                    row.model,
                    row.trained_at,
                ),
            )

def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value))
