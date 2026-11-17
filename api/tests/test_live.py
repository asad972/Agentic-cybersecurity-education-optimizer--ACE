"""WS /live (#39) against a fake Kafka consumer; no broker and no native client needed."""
import asyncio
import csv
import json
from pathlib import Path
import queue
import sys
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from ace_api.config import Settings
from ace_api.live import (ALERT_FIELDS, ALERT_TOPICS, ALERT_TYPES, COUNTRY, OVERFLOW, REQUIRED, AlertHub,
                          kafka_consumer, parse_alert)
from ace_api.main import create_app

SCHEMAS = Path(__file__).resolve().parents[2] / "shared" / "schemas"
BURST = {"country": "HU", "day": "2026-11-02", "created_at": "2026-11-02T12:00:00Z",
         "alert_type": "threat_burst", "subject": "phishing", "score": 0.91, "details": "HST=0.91"}
DRIFT = {"country": "EU", "day": "2026-11-02", "created_at": "2026-11-02T00:00:00+00:00",
         "alert_type": "drift", "subject": "Cloud security", "score": 3}


class Message:
    def __init__(self, value, topic="alerts.threats", error=None):
        self._value, self._topic, self._error = value, topic, error

    def value(self):
        return self._value

    def topic(self):
        return self._topic

    def error(self):
        return self._error


class Consumer:
    """Stands in for an AlertConsumer."""

    def __init__(self, down=False, missing=()):
        self.messages = queue.Queue()
        self.closed = False
        self.down, self.missing, self.follows = down, list(missing), 0

    def follow(self):
        self.follows += 1
        if self.down:
            raise RuntimeError("broker 10.0.0.5:9092 refused the connection")
        return list(self.missing)

    def push(self, payload, **message):
        raw = payload if payload is None or isinstance(payload, bytes) else json.dumps(payload).encode()
        self.messages.put(Message(raw, **message))

    def poll(self, timeout):
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        self.closed = True


class Kafka:
    """Consumer factory: a new fake consumer per connection; the first `failures` reach no broker."""

    def __init__(self, failures=0, missing=()):
        self.failures, self.missing = failures, missing
        self.consumers = []
        self.faults = []

    def __call__(self, settings, on_fault):
        self.faults.append(on_fault)
        self.consumers.append(Consumer(down=len(self.consumers) < self.failures, missing=self.missing))
        return self.consumers[-1]


class Database:
    def open(self):
        pass

    def close(self):
        pass

    def ready(self):
        return True


def serve(kafka=None, **settings):
    kafka = kafka if kafka is not None else Kafka()
    hub = AlertHub(Settings(live_retry_seconds=0.01, **settings), kafka)
    hub.poll_seconds = 0.01
    return TestClient(create_app(database=Database(), alerts=hub)), hub, kafka


def states(socket, until):
    """The status states received up to and including `until`."""
    seen = []
    while not seen or seen[-1] != until:
        message = socket.receive_json()
        assert message["type"] == "status", message
        seen.append(message["state"])
    return seen


def eventually(condition, seconds=5):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def test_new_alerts_reach_every_connected_browser():
    client, hub, kafka = serve()
    with client, client.websocket_connect("/live") as first:
        assert states(first, "connected") == ["connecting", "connected"]
        with client.websocket_connect("/live") as second:
            assert states(second, "connected") == ["connected"]
            kafka.consumers[0].push(BURST)
            kafka.consumers[0].push(DRIFT, topic="alerts.drift")
            for socket in (first, second):
                assert socket.receive_json() == {"type": "alert", "alert": BURST}
                assert socket.receive_json() == {"type": "alert", "alert": {**DRIFT, "details": ""}}
        assert len(kafka.consumers) == 1  # one Kafka reader for all browsers
    assert kafka.consumers[0].closed  # shutdown closes the consumer
    assert not hub._clients


def test_malformed_messages_are_skipped_not_forwarded():
    client, hub, kafka = serve()
    with client, client.websocket_connect("/live") as socket:
        states(socket, "connected")
        bad = (b"not json", b"[]", None, b"[" * 100_000,  # a tombstone; nesting that exhausts the parser
               {**BURST, "alert_type": "outage"}, {**BURST, "extra": 1})
        for payload in bad:
            kafka.consumers[0].push(payload)
        kafka.consumers[0].push(BURST)
        assert socket.receive_json() == {"type": "alert", "alert": BURST}
    assert hub.skipped == len(bad) and len(kafka.consumers) == 1


# ---- The alert check and the topic names against shared/schemas ----

ACCEPTED = [
    BURST, DRIFT, {**BURST, "country": "EU", "score": 0}, {**BURST, "score": -2.5e-3}, {**BURST, "subject": ""},
    {**BURST, "created_at": "2026-11-02T12:00:00.123456789+01:00"}, {**BURST, "created_at": "2026-11-02t12:00:00z"},
    {**BURST, "day": "2024-02-29"},
]
REJECTED = [{**BURST, **change} for change in (
    {"country": "hu"}, {"country": "HUN"}, {"country": None}, {"alert_type": "burst"}, {"subject": 7},
    {"day": "02/11/2026"}, {"day": 20261102}, {"day": "20261102"}, {"day": "2026-02-30"}, {"day": "2026-11-02T00:00:00Z"},
    {"created_at": "noon"}, {"created_at": "2026-11-02T12:00:00"}, {"created_at": "2026-11-02 12:00:00Z"},
    {"created_at": "2026-11-02T12:00Z"}, {"created_at": "2026-11-31T12:00:00Z"}, {"created_at": "2026-11-02T24:00:00Z"},
    {"score": "0.9"}, {"score": True}, {"score": None}, {"details": {"hst": 0.9}}, {"details": None}, {"unknown": 1},
)] + [{key: value for key, value in BURST.items() if key != field} for field in REQUIRED] + [["not", "an", "object"]]


@pytest.mark.parametrize("alert", ACCEPTED)
def test_parse_alert_accepts_alerts_that_fit_the_schema(alert):
    assert parse_alert(json.dumps(alert)) == {"details": "", **alert}


@pytest.mark.parametrize("alert", REJECTED)
def test_parse_alert_rejects_alerts_that_do_not_fit_the_schema(alert):
    with pytest.raises((ValueError, TypeError)):
        parse_alert(json.dumps(alert))


@pytest.mark.parametrize("score", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_parse_alert_rejects_numbers_json_does_not_have(score):
    # Python's json module reads these, and jsonschema would call them numbers.
    with pytest.raises(ValueError):
        parse_alert(json.dumps(BURST).replace("0.91", score))


def shared_schema(name):
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))


def test_parse_alert_agrees_with_the_shared_schema_validator():
    """The validator and format checks ace.contracts uses, so S6 and S8 publish exactly what is accepted here."""
    from jsonschema import Draft202012Validator

    checker = Draft202012Validator.FORMAT_CHECKER
    assert {"date", "date-time"} <= set(checker.checkers), "install the API's dev extra: jsonschema[format]"
    validator = Draft202012Validator(shared_schema("alert"), format_checker=checker)
    for alert in ACCEPTED + REJECTED:
        try:
            parse_alert(json.dumps(alert))
            accepted = True
        except (ValueError, TypeError):
            accepted = False
        assert accepted == validator.is_valid(alert), alert


def test_parse_alert_covers_every_rule_of_the_shared_schema():
    """Fails when alert.schema.json changes, so that parse_alert is changed with it."""
    schema = shared_schema("alert")
    assert (schema["type"], schema["additionalProperties"]) == ("object", False)
    assert tuple(schema["properties"]) == ALERT_FIELDS and set(schema["required"]) == set(REQUIRED)
    rules = {name: {key: value for key, value in rule.items() if key != "description"}
             for name, rule in schema["properties"].items()}
    assert set(rules["alert_type"].pop("enum")) == ALERT_TYPES
    assert rules == {
        "country": {"type": "string", "pattern": f"^{COUNTRY.pattern}$"},
        "day": {"type": "string", "format": "date"},
        "created_at": {"type": "string", "format": "date-time"},
        "alert_type": {"type": "string"},
        "subject": {"type": "string"},
        "score": {"type": "number"},
        "details": {"type": "string"},
    }


def test_alert_topics_are_the_shared_alert_topics():
    with open(SCHEMAS / "topics.csv", newline="", encoding="utf-8") as f:
        shared = [row["topic"] for row in csv.DictReader(f) if row["schema"] == "alert"]
    assert sorted(ALERT_TOPICS) == sorted(shared)


# ---- Kafka connection, outages and missing topics ----

def test_feed_reports_an_unreachable_kafka_and_recovers_without_leaking_details():
    client, hub, kafka = serve(Kafka(failures=2))
    with client, client.websocket_connect("/live") as socket:
        first = socket.receive_json()
        unavailable = socket.receive_json()
        assert (first["state"], unavailable["state"]) == ("connecting", "unavailable")
        assert "10.0.0.5" not in json.dumps(unavailable)
        assert states(socket, "connected") == ["connected"]
        assert [consumer.closed for consumer in kafka.consumers] == [True, True, False]
        kafka.consumers[-1].push(BURST)
        assert socket.receive_json()["alert"] == BURST


def test_a_missing_topic_does_not_stop_the_feed_and_is_looked_for_again():
    client, hub, kafka = serve(Kafka(missing=["alerts.threats"]))
    with client, client.websocket_connect("/live") as socket:
        assert states(socket, "connected") == ["connecting", "connected"]
        consumer = kafka.consumers[0]
        consumer.push(DRIFT, topic="alerts.drift")  # the topic that exists is followed
        assert socket.receive_json()["alert"]["subject"] == DRIFT["subject"]
        eventually(lambda: consumer.follows >= 3)  # looked for again every API_LIVE_RETRY_SECONDS
        consumer.missing = []
        eventually(lambda: hub._missing == [])
        follows = consumer.follows
        time.sleep(0.2)
        assert consumer.follows == follows  # then only every follow_seconds, for new partitions
        consumer.push(BURST)
        assert socket.receive_json() == {"type": "alert", "alert": BURST}  # no change of state in between
        assert kafka.consumers == [consumer]


def test_an_outage_is_reported_but_the_consumer_keeps_its_position():
    client, hub, kafka = serve()
    with client, client.websocket_connect("/live") as socket:
        states(socket, "connected")
        consumer = kafka.consumers[0]
        consumer.down = True
        kafka.faults[0](False)  # what the client's error callback does when no broker is left
        assert socket.receive_json()["state"] == "unavailable"
        consumer.push(BURST)    # the client reconnected by itself and fetched this before the probe succeeds
        assert socket.receive_json()["alert"] == BURST
        consumer.down = False
        assert states(socket, "connected") == ["connected"]
        consumer.push(DRIFT, topic="alerts.drift")
        assert socket.receive_json()["alert"]["subject"] == DRIFT["subject"]
        assert kafka.consumers == [consumer] and not consumer.closed  # never replaced, so nothing skipped


def test_a_failed_look_for_new_partitions_is_reported_as_an_outage():
    client, hub, kafka = serve()
    hub.follow_seconds = 0.01
    with client, client.websocket_connect("/live") as socket:
        states(socket, "connected")
        consumer = kafka.consumers[0]
        consumer.down = True
        assert socket.receive_json()["state"] == "unavailable"
        consumer.down = False
        assert states(socket, "connected") == ["connected"]
        assert kafka.consumers == [consumer] and not consumer.closed


@pytest.mark.parametrize("failure", ["fatal client error", "consumer error"])
def test_an_unrecoverable_consumer_is_closed_and_replaced(failure):
    client, hub, kafka = serve()
    with client, client.websocket_connect("/live") as socket:
        states(socket, "connected")
        if failure == "fatal client error":
            kafka.faults[0](True)
        else:
            kafka.consumers[0].push(BURST, error="KafkaError{code=UNKNOWN_TOPIC_OR_PART}")
        assert states(socket, "connected") == ["unavailable", "connected"]
        assert kafka.consumers[0].closed and len(kafka.consumers) == 2
        kafka.consumers[1].push(BURST)
        assert socket.receive_json()["alert"] == BURST


# ---- Browsers ----

def test_a_switched_off_feed_says_so_and_never_touches_kafka():
    client, hub, kafka = serve(live_alerts=False)
    with client, client.websocket_connect("/live") as socket:
        assert socket.receive_json()["state"] == "disabled"
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1000
    assert kafka.consumers == []


def test_rest_requests_alone_never_connect_to_kafka():
    client, hub, kafka = serve()
    with client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
    assert kafka.consumers == [] and hub.state == "connecting"


def test_only_configured_origins_may_open_the_feed():
    client, hub, kafka = serve()
    with client:
        with client.websocket_connect("/live", headers={"Origin": "http://localhost:5173"}) as socket:
            assert states(socket, "connected")[-1] == "connected"
        # Closed after the handshake with 1008, so the dashboard can tell it apart from a lost connection.
        with client.websocket_connect("/live", headers={"Origin": "https://ace.example"}) as socket:
            with pytest.raises(WebSocketDisconnect) as refused:
                socket.receive_json()
        assert (refused.value.code, refused.value.reason) == (1008, "Origin not allowed")
    assert len(kafka.consumers) == 1


def test_a_slow_browser_is_told_it_missed_alerts_and_memory_stays_bounded():
    hub = AlertHub(Settings(), Kafka())
    hub.state = "connected"
    slow = asyncio.Queue(maxsize=4)
    hub._clients.add(slow)
    for number in range(6):
        hub._publish({"type": "alert", "alert": {**BURST, "score": number}})
        assert slow.qsize() <= 4
    received = [slow.get_nowait() for _ in range(slow.qsize())]
    # 0-3 filled the queue; 4 emptied it and left a notice and the current state; 5 followed normally.
    # The notice is its own message type, so the next heartbeat does not replace it in the dashboard.
    assert received[:2] == [{"type": "skipped", "message": OVERFLOW}, hub.status()]
    assert [message["alert"]["score"] for message in received[2:]] == [4, 5]


def test_connections_are_capped():
    client, hub, kafka = serve()
    hub.max_clients = 1
    with client, client.websocket_connect("/live") as first:
        states(first, "connected")
        with client.websocket_connect("/live") as second:
            with pytest.raises(WebSocketDisconnect) as refused:
                second.receive_json()
            assert refused.value.code == 1013
        kafka.consumers[0].push(BURST)
        assert first.receive_json()["alert"] == BURST


def test_a_quiet_feed_sends_heartbeats():
    client, hub, kafka = serve()
    hub.heartbeat_seconds = 0.01
    with client, client.websocket_connect("/live") as socket:
        states(socket, "connected")
        assert [socket.receive_json()["state"] for _ in range(3)] == ["connected"] * 3


def test_kafka_settings_come_from_the_environment(monkeypatch):
    defaults = Settings.from_env()
    assert (defaults.kafka_bootstrap_servers, defaults.live_alerts, defaults.live_retry_seconds) == ("localhost:9092", True, 5)
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
    monkeypatch.setenv("API_LIVE_ALERTS", "False")
    monkeypatch.setenv("API_LIVE_RETRY_SECONDS", "2.5")
    settings = Settings.from_env()
    assert (settings.kafka_bootstrap_servers, settings.live_alerts, settings.live_retry_seconds) == ("kafka:29092", False, 2.5)
    for retry in ("0", "301", "nan", "soon"):
        monkeypatch.setenv("API_LIVE_RETRY_SECONDS", retry)
        with pytest.raises(ValueError):
            Settings.from_env()
    with pytest.raises(ValueError):
        Settings(kafka_bootstrap_servers=" ")


# ---- The real consumer factory, against a fake confluent_kafka module ----

class FakeKafkaClient:
    """Just enough of the confluent_kafka module for kafka_consumer() and AlertConsumer."""

    OFFSET_END = -1
    KafkaError = SimpleNamespace(_ALL_BROKERS_DOWN=-187)

    def __init__(self, topics):
        self.topics = topics
        self.down = False
        self.created = []
        client = self

        class TopicPartition:
            def __init__(self, topic, partition, offset):
                self.topic, self.partition, self.offset = topic, partition, offset

        class Consumer:
            def __init__(self, config):
                self.config, self.assigned, self.lookups, self.closed = config, [], [], False
                client.created.append(self)

            def list_topics(self, timeout):
                if client.down:
                    raise RuntimeError("KafkaError{code=_TRANSPORT}")
                return SimpleNamespace(topics=dict(client.topics))

            def offsets_for_times(self, partitions, timeout):
                self.lookups.append([(p.topic, p.partition, p.offset) for p in partitions])
                return [TopicPartition(p.topic, p.partition, 40 + p.partition) for p in partitions]

            def incremental_assign(self, partitions):
                pairs = {(p.topic, p.partition) for p in partitions}
                assert not pairs & {(topic, partition) for topic, partition, _ in self.assigned}, "assigned twice"
                self.assigned += [(p.topic, p.partition, p.offset) for p in partitions]

            def close(self):
                self.closed = True

        self.TopicPartition, self.Consumer = TopicPartition, Consumer


def topic(partitions=(0, 1, 2), error=None):
    return SimpleNamespace(error=error, partitions=dict.fromkeys(partitions))


@pytest.fixture
def fake_client(monkeypatch):
    def install(topics):
        client = FakeKafkaClient(topics)
        monkeypatch.setitem(sys.modules, "confluent_kafka", client)
        return client
    return install


def test_the_kafka_consumer_commits_nothing_and_passes_on_only_outages_and_fatal_errors(fake_client):
    kafka = fake_client({})
    faults = []
    kafka_consumer(Settings(kafka_bootstrap_servers="kafka:29092"), faults.append)
    config = kafka.created[0].config
    assert config["bootstrap.servers"] == "kafka:29092"
    assert config["enable.auto.commit"] is False  # the sink's offsets are never touched
    report = config["error_cb"]
    report(SimpleNamespace(fatal=lambda: False, code=lambda: -195))
    assert faults == []
    report(SimpleNamespace(fatal=lambda: False, code=lambda: kafka.KafkaError._ALL_BROKERS_DOWN))
    report(SimpleNamespace(fatal=lambda: True, code=lambda: -150))
    assert faults == [False, True]  # is the error fatal?


def test_the_consumer_follows_every_alert_partition_from_its_end(fake_client):
    kafka = fake_client({name: topic() for name in ALERT_TOPICS} | {"alerts.other": topic()})
    consumer = kafka_consumer(Settings(), lambda fatal: None)
    assert consumer.follow() == []
    assert kafka.created[0].assigned == [(name, partition, kafka.OFFSET_END)
                                         for name in ALERT_TOPICS for partition in (0, 1, 2)]
    assert kafka.created[0].lookups == []


@pytest.mark.parametrize("threats", [
    None,                       # alerts.threats does not exist
    topic(error="unknown"),     # the broker reports an error for it
    topic(partitions=()),       # it has no partitions yet
])
def test_a_missing_alert_topic_is_reported_and_the_other_is_followed(fake_client, threats):
    kafka = fake_client({"alerts.drift": topic()} | ({"alerts.threats": threats} if threats else {}))
    consumer = kafka_consumer(Settings(), lambda fatal: None)
    assert consumer.follow() == ["alerts.threats"]
    assert kafka.created[0].assigned == [("alerts.drift", partition, kafka.OFFSET_END) for partition in (0, 1, 2)]


def test_a_topic_or_partition_created_later_starts_at_the_first_alert_after_the_feed_connected(fake_client):
    kafka = fake_client({"alerts.drift": topic((0, 1))})
    consumer = kafka_consumer(Settings(), lambda fatal: None)
    assert consumer.follow() == ["alerts.threats"]
    since = consumer.since
    assert abs(since - time.time() * 1000) < 60_000
    kafka.topics = {"alerts.drift": topic((0, 1, 2)), "alerts.threats": topic((0,))}
    assert consumer.follow() == []
    real = kafka.created[0]
    assert real.lookups == [[("alerts.drift", 2, since), ("alerts.threats", 0, since)]]
    assert real.assigned[2:] == [("alerts.drift", 2, 42), ("alerts.threats", 0, 40)]  # the offsets Kafka named
    assert consumer.follow() == [] and len(real.assigned) == 4 and len(real.lookups) == 1  # nothing new


def test_follow_raises_when_no_broker_answers(fake_client):
    kafka = fake_client({name: topic() for name in ALERT_TOPICS})
    kafka.down = True
    consumer = kafka_consumer(Settings(), lambda fatal: None)
    with pytest.raises(RuntimeError):
        consumer.follow()
    assert kafka.created[0].assigned == [] and consumer.since is None
