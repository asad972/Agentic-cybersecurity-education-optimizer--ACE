"""Cassandra input and output boundary for the Skills Gap job."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from .config import GapConfig
from .types import GapRow, RoleRelation


SELECT_DEMAND_DAY = """
SELECT kind, item, count
FROM demand_by_day
WHERE country = ? AND day = ?
""".strip()

SELECT_SUPPLY_DAY = """
SELECT kind, item, count
FROM supply_by_day
WHERE country = ? AND day = ?
""".strip()

UPSERT_GAP = """
INSERT INTO skills_gap (country, month, skill, demand, supply, gap_ratio)
VALUES (?, ?, ?, ?, ?, ?)
""".strip()

UPSERT_RELATION = """
INSERT INTO role_relations (role, related_role, overlap)
VALUES (?, ?, ?)
""".strip()


@dataclass(frozen=True, slots=True)
class StoredCount:
    kind: str
    item: str
    count: int


@dataclass(frozen=True, slots=True)
class SupplySnapshot:
    day: date
    counts: tuple[StoredCount, ...]


class GapRepository(Protocol):
    def read_demand_day(self, country: str, day: date) -> tuple[StoredCount, ...]: ...
    def latest_supply(self, country: str, on_or_before: date, lookback_days: int) -> SupplySnapshot | None: ...
    def write_gaps(self, rows: Sequence[GapRow]) -> None: ...
    def write_relations(self, rows: Sequence[RoleRelation]) -> None: ...
    def close(self) -> None: ...


class CassandraGapRepository:
    def __init__(self, session, cluster=None):
        self._session = session
        self._cluster = cluster
        self._demand = session.prepare(SELECT_DEMAND_DAY)
        self._supply = session.prepare(SELECT_SUPPLY_DAY)
        self._gap = session.prepare(UPSERT_GAP)
        self._relation = session.prepare(UPSERT_RELATION)

    @classmethod
    def connect(cls, config: GapConfig) -> "CassandraGapRepository":
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

    def read_demand_day(self, country: str, day: date) -> tuple[StoredCount, ...]:
        rows = self._session.execute(self._demand, (country, day))
        return tuple(StoredCount(str(row.kind), str(row.item), int(row.count)) for row in rows)

    def latest_supply(self, country: str, on_or_before: date, lookback_days: int) -> SupplySnapshot | None:
        if not 1 <= lookback_days <= 365:
            raise ValueError("lookback_days must be between 1 and 365")
        for offset in range(lookback_days):
            candidate = on_or_before - timedelta(days=offset)
            rows = tuple(
                StoredCount(str(row.kind), str(row.item), int(row.count))
                for row in self._session.execute(self._supply, (country, candidate))
            )
            if rows:
                return SupplySnapshot(candidate, rows)
        return None

    def write_gaps(self, rows: Sequence[GapRow]) -> None:
        for row in rows:
            self._session.execute(
                self._gap,
                (row.country, row.month, row.skill, row.demand, row.supply, row.gap_ratio),
            )

    def write_relations(self, rows: Sequence[RoleRelation]) -> None:
        for row in rows:
            self._session.execute(self._relation, (row.role, row.related_role, row.overlap))

    def close(self) -> None:
        if self._cluster is not None:
            self._cluster.shutdown()

    def __enter__(self) -> "CassandraGapRepository":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
