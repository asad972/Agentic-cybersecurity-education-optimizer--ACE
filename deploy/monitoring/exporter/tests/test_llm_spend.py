from collections import namedtuple
from datetime import datetime, timedelta, timezone

import pytest

from ace_exporter.llm_spend import QUERY, Spend, SpendState, check, day_start, spend_by_task

Row = namedtuple("Row", "task provider cost_eur created_at")

NOW = datetime(2026, 11, 8, 14, 30, tzinfo=timezone.utc)
MIDNIGHT = datetime(2026, 11, 8, tzinfo=timezone.utc)


def test_day_start_is_midnight_utc():
    assert day_start(NOW) == MIDNIGHT
    # 00:30 in Budapest is still the previous day in UTC.
    budapest = timezone(timedelta(hours=1))
    assert day_start(datetime(2026, 11, 8, 0, 30, tzinfo=budapest)) == datetime(2026, 11, 7, tzinfo=timezone.utc)
    # A naive time is read as UTC.
    assert day_start(datetime(2026, 11, 8, 23, 59)) == MIDNIGHT


def test_spend_is_summed_per_task_and_provider_for_today_only():
    rows = [
        Row("skill_extraction", "hosted", 0.002, datetime(2026, 11, 8, 9, 0)),
        Row("skill_extraction", "hosted", 0.003, datetime(2026, 11, 8, 10, 0)),
        Row("skill_extraction", "local", 0.0, datetime(2026, 11, 8, 10, 0)),
        Row("programme_roles", "hosted", 0.01, datetime(2026, 11, 8, 0, 0)),          # midnight counts
        Row("skill_extraction", "hosted", 5.0, datetime(2026, 11, 7, 23, 59, 59)),    # yesterday
    ]
    totals = spend_by_task(rows, MIDNIGHT)
    assert totals.keys() == {("skill_extraction", "hosted"), ("skill_extraction", "local"),
                             ("programme_roles", "hosted")}
    assert totals[("skill_extraction", "hosted")].eur == pytest.approx(0.005)
    assert totals[("skill_extraction", "hosted")].calls == 2
    assert totals[("skill_extraction", "local")] == Spend(0.0, 1)
    assert totals[("programme_roles", "hosted")] == Spend(0.01, 1)


def test_missing_cost_counts_as_zero_and_missing_names_as_unknown():
    rows = [Row(None, None, None, datetime(2026, 11, 8, 9, 0)), Row("t", "local", 1.0, None)]
    assert spend_by_task(rows, MIDNIGHT) == {("unknown", "unknown"): Spend(0.0, 1)}


def test_timezone_aware_rows_are_compared_in_utc():
    budapest = timezone(timedelta(hours=1))
    rows = [Row("t", "hosted", 1.0, datetime(2026, 11, 8, 0, 30, tzinfo=budapest))]   # 23:30 UTC the day before
    assert spend_by_task(rows, MIDNIGHT) == {}


class FakeSession:
    def __init__(self, rows=(), error=None):
        self.rows, self.error, self.calls = list(rows), error, []

    def execute(self, query, params):
        self.calls.append((query, params))
        if self.error:
            raise self.error
        return iter(self.rows)


def test_check_queries_llm_cache_from_midnight():
    session = FakeSession([Row("skill_extraction", "hosted", 0.5, datetime(2026, 11, 8, 9, 0))])
    state = check(lambda: session, NOW)
    assert session.calls == [(QUERY, (MIDNIGHT,))]
    assert state == SpendState(True, True, {("skill_extraction", "hosted"): Spend(0.5, 1)}, NOW)


def test_an_empty_table_means_no_spend():
    assert check(lambda: FakeSession(), NOW) == SpendState(True, True, {}, NOW)


class InvalidRequest(Exception):
    pass


class NoHostAvailable(Exception):
    pass


def test_a_missing_table_is_reported_not_raised():
    state = check(lambda: FakeSession(error=InvalidRequest("unconfigured table llm_cache")), NOW)
    assert state == SpendState(cassandra_up=True, available=False, by_task={}, checked_at=NOW)


def test_losing_cassandra_during_the_query_marks_it_down():
    state = check(lambda: FakeSession(error=NoHostAvailable("all hosts down")), NOW)
    assert state == SpendState(cassandra_up=False, available=False, by_task={}, checked_at=NOW)


def test_failing_to_connect_is_reported_not_raised():
    def connect():
        raise NoHostAvailable("no Cassandra")

    assert check(connect, NOW) == SpendState(False, False, {}, NOW)
