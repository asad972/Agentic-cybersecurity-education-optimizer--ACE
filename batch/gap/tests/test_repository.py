from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from ace_gap.repository import (
    CassandraGapRepository,
    SELECT_DEMAND_DAY,
    SELECT_SUPPLY_DAY,
    UPSERT_GAP,
    UPSERT_RELATION,
)
from ace_gap.types import GapRow, RoleRelation


class FakeSession:
    def __init__(self, results=None, error=None):
        self.results = results or {}
        self.error = error
        self.prepared = []
        self.executed = []

    def prepare(self, statement):
        self.prepared.append(statement)
        return f"prepared-{len(self.prepared)}"

    def execute(self, statement, parameters):
        if self.error:
            raise self.error
        key = (statement, tuple(parameters))
        self.executed.append(key)
        return self.results.get(key, ())


def row(kind="skill", item="Linux", count=4):
    return SimpleNamespace(kind=kind, item=item, count=count)


def test_prepares_contract_queries_and_reads_complete_demand_partition():
    day = date(2026, 10, 1)
    session = FakeSession({("prepared-1", ("DE", day)): [row(), row("certification", "CISSP", 2)]})
    repository = CassandraGapRepository(session)
    result = repository.read_demand_day("DE", day)
    assert session.prepared == [SELECT_DEMAND_DAY, SELECT_SUPPLY_DAY, UPSERT_GAP, UPSERT_RELATION]
    assert [(item.kind, item.item, item.count) for item in result] == [
        ("skill", "Linux", 4),
        ("certification", "CISSP", 2),
    ]


def test_latest_supply_searches_backwards_and_stops_at_first_partition():
    end = date(2026, 10, 31)
    session = FakeSession({("prepared-2", ("GB", end - timedelta(days=2))): [row(count=21)]})
    repository = CassandraGapRepository(session)
    snapshot = repository.latest_supply("GB", end, 365)
    assert snapshot is not None
    assert snapshot.day == date(2026, 10, 29)
    assert len(session.executed) == 3
    assert [parameters[1] for _, parameters in session.executed] == [
        end,
        end - timedelta(days=1),
        end - timedelta(days=2),
    ]


def test_supply_lookback_is_inclusive_and_bounded():
    end = date(2026, 10, 31)
    boundary = end - timedelta(days=364)
    session = FakeSession({("prepared-2", ("DE", boundary)): [row(count=20)]})
    repository = CassandraGapRepository(session)
    assert repository.latest_supply("DE", end, 365).day == boundary

    too_old = FakeSession({("prepared-2", ("DE", end - timedelta(days=365))): [row()]})
    assert CassandraGapRepository(too_old).latest_supply("DE", end, 365) is None
    with pytest.raises(ValueError):
        repository.latest_supply("DE", end, 366)


def test_gap_and_relation_upserts_are_prepared_and_idempotent():
    session = FakeSession()
    repository = CassandraGapRepository(session)
    gap = GapRow("DE", "2026-10", "Linux", 40, 20, 2.0)
    relation = RoleRelation("Architect", "Implementer", 0.5)
    repository.write_gaps([gap, gap])
    repository.write_relations([relation, relation])
    assert session.executed[0] == session.executed[1] == (
        "prepared-3",
        ("DE", "2026-10", "Linux", 40, 20, 2.0),
    )
    assert session.executed[2] == session.executed[3] == (
        "prepared-4",
        ("Architect", "Implementer", 0.5),
    )


def test_database_errors_are_not_swallowed():
    repository = CassandraGapRepository(FakeSession(error=RuntimeError("database unavailable")))
    with pytest.raises(RuntimeError, match="database unavailable"):
        repository.read_demand_day("DE", date(2026, 10, 1))
