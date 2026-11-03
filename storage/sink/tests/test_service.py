import json
import sys
from datetime import datetime, timezone

import pytest

from ace import contracts
from ace_sink import rows as sink_rows
from ace_sink.rows import Unstorable
from ace_sink.service import Settings, handle, run

RECEIVED = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
COMPANIES = {"country": "NL", "day": "2026-10-01", "companies_day": 31, "companies_30d": 210}

# Every topic docs/ARCHITECTURE.md (Storage) says the sink writes.
SINK_TOPICS = {
    "jobs.clean", "raw.profiles", "raw.programmes", "raw.workforce",
    "agg.demand_daily", "agg.supply_daily", "agg.top10_demand",
    "agg.threats_hourly", "agg.threats_daily", "agg.emerging_skills", "agg.companies_daily",
    "alerts.drift", "alerts.threats", "analysis.platforms", "tasks.collect",
}


class Writer:
    def __init__(self, error=None):
        self.rows = []
        self.error = error

    def write(self, rows):
        if self.error:
            raise self.error
        self.rows.extend(rows)


class Record:
    """Stands in for a confluent_kafka Message."""

    def __init__(self, topic, value):
        self._topic, self._value = topic, value

    def topic(self):
        return self._topic

    def value(self):
        return self._value

    def error(self):
        return None


class Consumer:
    def __init__(self, records):
        self.records = list(records)
        self.subscribed = None
        self.commits = []

    def subscribe(self, topics):
        self.subscribed = topics

    def poll(self, timeout):
        return self.records.pop(0) if self.records else None

    def commit(self, message, asynchronous):
        self.commits.append((message, asynchronous))


class Producer:
    def __init__(self, delivery_error=None, undelivered=0):
        self.sent = []
        self.callbacks = []
        self.delivery_error = delivery_error
        self.undelivered = undelivered

    def produce(self, topic, key, value, on_delivery):
        self.sent.append((topic, key, json.loads(value)))
        self.callbacks.append(on_delivery)

    def flush(self, timeout):
        for callback in self.callbacks:
            callback(self.delivery_error, None)
        self.callbacks = []
        return self.undelivered


def settings(**changes):
    return Settings.from_env({"SINK_TOPICS": "agg.companies_daily,agg.threats_hourly", **changes})


def until_empty(consumer):
    return lambda: not consumer.records


def companies_record(message=COMPANIES):
    return Record("agg.companies_daily", json.dumps(message).encode())


def test_sink_writes_every_topic_the_architecture_gives_it():
    assert set(sink_rows.topics()) == SINK_TOPICS


def test_valid_message_is_written():
    writer = Writer()
    assert handle("agg.companies_daily", json.dumps(COMPANIES).encode(), RECEIVED, writer) is None
    assert [row.table for row in writer.rows] == ["companies_by_day"]


def test_message_that_is_not_json_is_invalid():
    writer = Writer()
    invalid = handle("agg.companies_daily", b"{not json", RECEIVED, writer)
    assert invalid["topic"] == "agg.companies_daily"
    assert invalid["producer"] == "sink"
    assert invalid["reason"].startswith("not JSON")
    assert invalid["payload"] == "{not json"
    assert not writer.rows


def test_message_that_fails_its_schema_is_invalid():
    invalid = handle("agg.companies_daily", json.dumps({"country": "NL"}).encode(), RECEIVED, Writer())
    assert "required property" in invalid["reason"]
    contracts.validate(contracts.INVALID_TOPIC, invalid)


def test_message_that_cannot_become_rows_is_invalid():
    message = {"country": "DE", "day": "2026-10-01", "counts": {"malware": 9}}  # hourly, but no hour
    invalid = handle("agg.threats_hourly", json.dumps(message).encode(), RECEIVED, Writer())
    assert invalid["reason"] == "cannot be stored: an agg.threats_hourly message needs an hour"


def test_value_cassandra_cannot_bind_is_invalid():
    writer = Writer(error=Unstorable("companies_by_day: invalid type for column"))
    invalid = handle("agg.companies_daily", json.dumps(COMPANIES).encode(), RECEIVED, writer)
    assert invalid["reason"] == "cannot be stored: companies_by_day: invalid type for column"


def test_bug_in_a_row_function_stops_the_sink(monkeypatch):
    def broken(message, received_at):
        return message["typo"]

    monkeypatch.setitem(sink_rows._functions, "agg.companies_daily", broken)
    with pytest.raises(KeyError, match="typo"):
        handle("agg.companies_daily", json.dumps(COMPANIES).encode(), RECEIVED, Writer())


def test_run_commits_each_message_after_writing_it_and_once_more_at_the_end():
    records = [companies_record(), companies_record()]
    consumer, writer = Consumer(records), Writer()
    run(settings(), consumer, Producer(), writer, stop=until_empty(consumer))
    assert consumer.subscribed == ["agg.companies_daily", "agg.threats_hourly"]
    assert consumer.commits == [(records[0], True), (records[1], True), (records[1], False)]
    assert len(writer.rows) == 2


def test_run_sends_invalid_messages_to_errors_invalid_and_moves_on():
    records = [Record("agg.companies_daily", b"oops"), companies_record()]
    consumer, producer, writer = Consumer(records), Producer(), Writer()
    run(settings(), consumer, producer, writer, stop=until_empty(consumer))
    [(topic, key, invalid)] = producer.sent
    assert (topic, key) == ("errors.invalid", "agg.companies_daily")
    assert invalid["payload"] == "oops"
    assert [message for message, _ in consumer.commits] == [records[0], records[1], records[1]]
    assert len(writer.rows) == 1


@pytest.mark.parametrize("producer", [Producer(delivery_error="MSG_SIZE_TOO_LARGE"), Producer(undelivered=1)])
def test_run_stops_without_commit_when_errors_invalid_does_not_take_the_record(producer):
    consumer = Consumer([Record("agg.companies_daily", b"oops")])
    with pytest.raises(RuntimeError, match="errors.invalid did not accept"):
        run(settings(), consumer, producer, Writer(), stop=until_empty(consumer))
    assert consumer.commits == []


def test_run_stops_without_commit_when_cassandra_fails():
    consumer = Consumer([companies_record()])
    with pytest.raises(TimeoutError):
        run(settings(), consumer, Producer(), Writer(error=TimeoutError("no Cassandra")), stop=until_empty(consumer))
    assert consumer.commits == []


def test_received_time_is_when_the_sink_read_the_message():
    message = {"programme_id": "p1", "country": "NL", "name": "MSc", "institution": "TU", "ecsf_roles": []}
    consumer, writer = Consumer([Record("raw.programmes", json.dumps(message).encode())]), Writer()
    run(settings(SINK_TOPICS="raw.programmes"), consumer, Producer(), writer,
        stop=until_empty(consumer), clock=lambda: RECEIVED)
    assert writer.rows[0].values["updated_at"] == RECEIVED


def test_settings_default_to_every_topic_and_reject_unknown_ones():
    assert set(Settings.from_env({}).topics) == SINK_TOPICS
    assert Settings.from_env({"CASSANDRA_HOSTS": "cassandra-0, cassandra-1"}).cassandra_hosts == (
        "cassandra-0", "cassandra-1")
    with pytest.raises(ValueError, match="no row function for \\['raw.jobs'\\]"):
        Settings.from_env({"SINK_TOPICS": "raw.jobs"})


def test_tests_never_load_the_cassandra_or_kafka_libraries():
    assert "cassandra" not in sys.modules
    assert "confluent_kafka" not in sys.modules
