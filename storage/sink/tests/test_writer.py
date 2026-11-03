import pytest

from ace_sink.rows import Row, Unstorable
from ace_sink.writer import CassandraWriter


class Future:
    def __init__(self, error=None):
        self.error = error

    def result(self):
        if self.error:
            raise self.error


class Prepared:
    """Stands in for a prepared statement; like the driver, it refuses a float for an int."""

    def __init__(self, cql):
        self.cql = cql

    def bind(self, values):
        if any(isinstance(value, float) for value in values):
            raise TypeError("Received an argument of invalid type for column")
        return self.cql, values


class Session:
    """Stands in for a cassandra-driver session; records what would be written."""

    def __init__(self, error=None):
        self.prepared = []
        self.writes = []
        self.error = error

    def prepare(self, cql):
        self.prepared.append(cql)
        return Prepared(cql)

    def execute_async(self, bound):
        self.writes.append(bound)
        return Future(self.error)


def test_row_becomes_an_insert_with_its_values():
    session = Session()
    CassandraWriter(session).write([Row("job_ads_by_day", {"day": "d", "country": "DE", "count": 12})])
    assert session.writes == [("INSERT INTO job_ads_by_day (day, country, count) VALUES (?, ?, ?)", ("d", "DE", 12))]


def test_each_statement_is_prepared_once():
    session = Session()
    writer = CassandraWriter(session)
    row = Row("job_ads_by_day", {"day": "d", "country": "DE", "count": 12})
    writer.write([row, row])
    writer.write([row])
    assert len(session.prepared) == 1
    assert len(session.writes) == 3


def test_none_is_written_as_null_so_an_old_value_is_cleared():
    session = Session()
    CassandraWriter(session).write([Row("workforce_baseline", {"country": "DE", "year": 2025, "workforce_gap": None})])
    assert session.writes == [
        ("INSERT INTO workforce_baseline (country, year, workforce_gap) VALUES (?, ?, ?)", ("DE", 2025, None))
    ]


def test_value_that_cannot_be_bound_is_unstorable_and_nothing_is_written():
    session = Session()
    rows = [Row("job_ads_by_day", {"day": "d", "country": "DE", "count": 12}),
            Row("job_ads_by_day", {"day": "d", "country": "FR", "count": 3.0})]
    with pytest.raises(Unstorable, match="job_ads_by_day: Received an argument of invalid type"):
        CassandraWriter(session).write(rows)
    assert session.writes == []


def test_bad_table_or_column_name_is_refused():
    with pytest.raises(ValueError, match="not a table or column name"):
        CassandraWriter(Session()).write([Row("t; DROP TABLE x", {"a": 1})])


def test_failed_write_raises():
    with pytest.raises(TimeoutError):
        CassandraWriter(Session(error=TimeoutError("write timed out"))).write([Row("t", {"a": 1})])
