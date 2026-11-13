from datetime import datetime, timezone

import pytest

from ace import contracts
from ace_sink import rows
from ace_sink.rows import Row, rows_for

RECEIVED = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def registry(monkeypatch):
    """An empty registry, so these tests do not depend on the real row functions."""
    monkeypatch.setattr(rows, "_functions", {})


def test_registered_function_turns_a_message_into_rows(registry):
    @rows_for("agg.companies_daily")
    def companies(message, received_at):
        return [Row("companies_by_day", {"day": message["day"], "country": message["country"]})]

    assert rows.rows("agg.companies_daily", {"day": "2026-10-01", "country": "DE"}, RECEIVED) == [
        Row("companies_by_day", {"day": "2026-10-01", "country": "DE"})
    ]
    assert rows.topics() == ["agg.companies_daily"]


def test_row_function_gets_the_time_kafka_received_the_message(registry):
    rows_for("raw.workforce")(lambda message, received_at: [Row("t", {"at": received_at})])
    assert rows.rows("raw.workforce", {}, RECEIVED) == [Row("t", {"at": RECEIVED})]


def test_unknown_topic_name_fails_when_registering(registry):
    with pytest.raises(contracts.UnknownTopic, match="did you mean 'agg.companies_daily'"):
        rows_for("agg.company_daily")


def test_a_topic_has_only_one_row_function(registry):
    rows_for("alerts.drift")(lambda message, received_at: [])
    with pytest.raises(ValueError, match="already has a row function"):
        rows_for("alerts.drift")(lambda message, received_at: [])


def test_topic_without_row_function_is_an_error(registry):
    with pytest.raises(KeyError, match="no row function for raw.jobs"):
        rows.rows("raw.jobs", {}, RECEIVED)
