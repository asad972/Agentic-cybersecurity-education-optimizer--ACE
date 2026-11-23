import json

import pytest

from ace_exporter.counts import TOPICS, PipelineCounts
from ace_exporter.service import Settings, consume


def test_settings_defaults():
    s = Settings.from_env({})
    assert s.kafka_bootstrap == "localhost:9092"
    assert s.group_id == "ace-monitoring"
    assert s.port == 9400
    assert s.cassandra_hosts == ("localhost",)
    assert s.cassandra_keyspace == "ace"
    assert s.spend_interval_seconds == 300
    assert s.daily_budget_eur is None


def test_settings_from_environment():
    s = Settings.from_env({
        "KAFKA_BOOTSTRAP_SERVERS": "kafka:29092",
        "EXPORTER_GROUP_ID": "ace-monitoring-test",
        "EXPORTER_PORT": "9500",
        "CASSANDRA_HOSTS": "cassandra-0, cassandra-1",
        "LLM_SPEND_INTERVAL_SECONDS": "60",
        "LLM_DAILY_BUDGET_EUR": "2          # LLM module stops calling once today's spend reaches this",
    })
    assert s.kafka_bootstrap == "kafka:29092"
    assert s.group_id == "ace-monitoring-test"
    assert s.port == 9500
    assert s.cassandra_hosts == ("cassandra-0", "cassandra-1")
    assert s.spend_interval_seconds == 60
    assert s.daily_budget_eur == 2.0


def test_a_budget_that_is_not_a_number_is_left_out():
    assert Settings.from_env({"LLM_DAILY_BUDGET_EUR": "two"}).daily_budget_eur is None


def test_spend_interval_must_be_positive():
    with pytest.raises(ValueError):
        Settings.from_env({"LLM_SPEND_INTERVAL_SECONDS": "0"})


def test_the_consumer_only_reads():
    config = Settings.from_env({}).consumer_config()
    assert config["group.id"] == "ace-monitoring"
    assert config["auto.offset.reset"] == "earliest"
    assert not any(key.startswith(("transactional", "acks")) for key in config)


class Record:
    def __init__(self, topic, value, error=None):
        self._topic, self._value, self._error = topic, value, error

    def topic(self):
        return self._topic

    def value(self):
        return self._value

    def error(self):
        return self._error


class FakeConsumer:
    def __init__(self, records):
        self.records = list(records)
        self.subscribed = None

    def subscribe(self, topics):
        self.subscribed = topics

    def poll(self, timeout):
        return self.records.pop(0) if self.records else None


def test_consume_counts_every_message_and_skips_errors():
    consumer = FakeConsumer([
        Record("raw.jobs", json.dumps({"source": "adzuna"}).encode()),
        None,
        Record("raw.jobs", None, error="broker down"),
        Record("raw.jobs", json.dumps({"source": "adzuna"}).encode()),
    ])
    counts = PipelineCounts()
    consume(consumer, counts, stop=lambda: not consumer.records)

    assert consumer.subscribed == list(TOPICS)
    assert counts.snapshot().records == {("raw.jobs", "adzuna"): 2}
