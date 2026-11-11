"""Cassandra boundary for Demand input and forecast output."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from .config import ForecastConfig
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


class CassandraForecastRepository:
    def __init__(self, session, cluster=None):
        self._session = session
        self._cluster = cluster
        self._select = session.prepare(SELECT_DEMAND)
        self._upsert = session.prepare(UPSERT_FORECAST)

    @classmethod
    def connect(cls, config: ForecastConfig) -> "CassandraForecastRepository":
        from cassandra.auth import PlainTextAuthProvider
        from cassandra.cluster import Cluster
        from cassandra.policies import DCAwareRoundRobinPolicy

        auth = None
        if config.cassandra_username is not None:
            auth = PlainTextAuthProvider(config.cassandra_username, config.cassandra_password or "")
        cluster = Cluster(
            list(config.cassandra_hosts),
            port=config.cassandra_port,
            protocol_version=4,
            load_balancing_policy=DCAwareRoundRobinPolicy(local_dc=config.cassandra_local_dc),
            auth_provider=auth,
        )
        try:
            session = cluster.connect(config.cassandra_keyspace)
        except Exception:
            cluster.shutdown()
            raise
        return cls(session, cluster)

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

    def close(self) -> None:
        if self._cluster is not None:
            self._cluster.shutdown()

    def __enter__(self) -> "CassandraForecastRepository":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value))
