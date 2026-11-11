from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from ace_forecast.repository import CassandraForecastRepository, SELECT_DEMAND, UPSERT_FORECAST
from ace_forecast.types import ForecastRow


class FakeSession:
    def __init__(self, demand=()):
        self.demand = demand
        self.prepared = []
        self.executed = []

    def prepare(self, statement):
        self.prepared.append(statement)
        return f"prepared-{len(self.prepared)}"

    def execute(self, statement, parameters):
        self.executed.append((statement, parameters))
        if statement == "prepared-1":
            return self.demand
        return ()


def test_query_uses_complete_partition_key_and_sorts_results():
    session = FakeSession([
        SimpleNamespace(day=date(2026, 2, 1), count=2),
        SimpleNamespace(day=date(2026, 1, 1), count=1),
    ])
    repository = CassandraForecastRepository(session)
    points = repository.read_daily("DE", "Linux")
    assert session.prepared == [SELECT_DEMAND, UPSERT_FORECAST]
    assert session.executed == [("prepared-1", ("DE", "skill", "Linux"))]
    assert [point.day for point in points] == [date(2026, 1, 1), date(2026, 2, 1)]


def test_prepared_upserts_use_existing_schema_and_shared_timestamp():
    session = FakeSession()
    repository = CassandraForecastRepository(session)
    trained_at = datetime(2026, 11, 11, tzinfo=timezone.utc)
    row = ForecastRow("DE", "Linux", "2026-12", 4, 2, 6, "sarima", trained_at)
    repository.write([row, row])
    assert session.executed[0][0] == "prepared-2"
    assert session.executed[0][1] == ("DE", "Linux", "2026-12", 4, 2, 6, "sarima", trained_at)
    assert session.executed[0] == session.executed[1]


def test_database_errors_are_not_swallowed():
    class Broken(FakeSession):
        def execute(self, statement, parameters):
            raise RuntimeError("database unavailable")

    repository = CassandraForecastRepository(Broken())
    with pytest.raises(RuntimeError, match="database unavailable"):
        repository.read_daily("DE", "Linux")
