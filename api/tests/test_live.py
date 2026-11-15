"""WS /live (#39) against a fake Kafka consumer; no broker and no native client needed."""
import asyncio
import json
import queue
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from ace_api.config import Settings
from ace_api.live import ALERT_TOPICS, OVERFLOW, AlertHub, kafka_consumer, parse_alert
from ace_api.main import create_app

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
    """Stands in for a confluent_kafka Consumer that is already assigned."""

    def __init__(self):
        self.messages = queue.Queue()
        self.closed = self.down = False

    def list_topics(self, topic, timeout):
        if self.down:
            raise RuntimeError("timed out")
        return SimpleNamespace(topics={topic: None})

    def push(self, payload, **message):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.messages.put(Message(raw, **message))

    def poll(self, timeout):
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        self.closed = True


class Kafka:
    """Consumer factory: hands out a new fake consumer per connection, or fails first."""

    def __init__(self, failures=0):
        self.failures = failures
        self.consumers = []
        self.faults = []

    def __call__(self, settings, on_fault):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("broker 10.0.0.5:9092 refused the connection")
        self.faults.append(on_fault)
        self.consumers.append(Consumer())
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
        for bad in (b"not json", b"[]", {**BURST, "alert_type": "outage"}, {**BURST, "extra": 1}):
            kafka.consumers[0].push(bad)
        kafka.consumers[0].push(BURST)
        assert socket.receive_json() == {"type": "alert", "alert": BURST}
    assert hub.skipped == 4


@pytest.mark.parametrize("alert", [BURST, DRIFT, {**BURST, "country": "EU", "score": 0}])
def test_parse_alert_accepts_the_shared_alert_schema(alert):
    assert parse_alert(json.dumps(alert)) == {"details": "", **alert}


@pytest.mark.parametrize("change", [
    {"country": "hu"}, {"country": "HUN"}, {"country": None}, {"day": "02/11/2026"}, {"day": 20261102},
    {"created_at": "noon"}, {"alert_type": "burst"}, {"subject": ""}, {"subject": 7},
    {"score": "0.9"}, {"score": True}, {"score": float("nan")}, {"score": float("inf")},
    {"details": {"hst": 0.9}}, {"unknown": 1},
])
def test_parse_alert_rejects_what_the_schema_rejects(change):
    with pytest.raises((ValueError, TypeError)):
        parse_alert(json.dumps({**BURST, **change}))
    for field in ("country", "day", "created_at", "alert_type", "subject", "score"):
        with pytest.raises((ValueError, TypeError)):
            parse_alert(json.dumps({key: value for key, value in BURST.items() if key != field}))


def test_feed_reports_an_unreachable_kafka_and_recovers_without_leaking_details():
    client, hub, kafka = serve(Kafka(failures=2))
    with client, client.websocket_connect("/live") as socket:
        first = socket.receive_json()
        unavailable = socket.receive_json()
        assert (first["state"], unavailable["state"]) == ("connecting", "unavailable")
        assert "10.0.0.5" not in json.dumps(unavailable)
        assert states(socket, "connected") == ["connected"]
        kafka.consumers[0].push(BURST)
        assert socket.receive_json()["alert"] == BURST


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
        with pytest.raises(WebSocketDisconnect) as refused:
            with client.websocket_connect("/live", headers={"Origin": "https://evil.example"}):
                pass
        assert refused.value.code == 1008
    assert len(kafka.consumers) == 1


def test_a_slow_browser_is_told_it_missed_alerts_and_memory_stays_bounded():
    hub = AlertHub(Settings(), Kafka())
    hub.state = "connected"
    slow = asyncio.Queue(maxsize=3)
    hub._clients.add(slow)
    for number in range(5):
        hub._publish({"type": "alert", "alert": {**BURST, "score": number}})
        assert slow.qsize() <= 3
    received = [slow.get_nowait() for _ in range(slow.qsize())]
    # 0-2 filled the queue; 3 emptied it and left a notice; 4 followed normally.
    assert received[0] == {"type": "status", "state": "connected", "message": OVERFLOW}
    assert [message["alert"]["score"] for message in received[1:]] == [3, 4]


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
    assert (Settings().kafka_bootstrap_servers, Settings().live_alerts) == ("localhost:9092", True)
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
    monkeypatch.setenv("API_LIVE_ALERTS", "False")
    settings = Settings.from_env()
    assert (settings.kafka_bootstrap_servers, settings.live_alerts) == ("kafka:29092", False)
    for invalid in ({"kafka_bootstrap_servers": " "}, {"live_retry_seconds": 0}):
        with pytest.raises(ValueError):
            Settings(**invalid)


class FakeKafkaClient:
    """Just enough of the confluent_kafka module for kafka_consumer()."""

    OFFSET_END = -1
    KafkaError = SimpleNamespace(_ALL_BROKERS_DOWN=-187)

    def __init__(self, topics):
        self.topics = topics
        self.created = []
        client = self

        class TopicPartition:
            def __init__(self, topic, partition, offset):
                self.topic, self.partition, self.offset = topic, partition, offset

        class Consumer:
            def __init__(self, config):
                self.config, self.assigned, self.closed = config, None, False
                client.created.append(self)

            def list_topics(self, topic, timeout):
                return SimpleNamespace(topics={topic: client.topics[topic]} if topic in client.topics else {})

            def assign(self, partitions):
                self.assigned = [(p.topic, p.partition, p.offset) for p in partitions]

            def close(self):
                self.closed = True

        self.TopicPartition, self.Consumer = TopicPartition, Consumer


def topic(partitions=(0, 1, 2), error=None):
    return SimpleNamespace(error=error, partitions=dict.fromkeys(partitions))


def test_the_kafka_consumer_follows_every_alert_partition_from_its_end(monkeypatch):
    kafka = FakeKafkaClient({name: topic() for name in ALERT_TOPICS})
    monkeypatch.setitem(sys.modules, "confluent_kafka", kafka)
    faults = []
    consumer = kafka_consumer(Settings(kafka_bootstrap_servers="kafka:29092"), faults.append)
    assert consumer.config["bootstrap.servers"] == "kafka:29092"
    assert consumer.config["enable.auto.commit"] is False  # the sink's offsets are never touched
    assert consumer.assigned == [(name, partition, kafka.OFFSET_END)
                                 for name in ALERT_TOPICS for partition in (0, 1, 2)]
    # Only the loss of every broker (or a fatal error) is passed on to the hub.
    report = consumer.config["error_cb"]
    report(SimpleNamespace(fatal=lambda: False, code=lambda: -195))
    assert faults == []
    report(SimpleNamespace(fatal=lambda: False, code=lambda: kafka.KafkaError._ALL_BROKERS_DOWN))
    report(SimpleNamespace(fatal=lambda: True, code=lambda: -150))
    assert faults == [False, True]  # is the error fatal?


@pytest.mark.parametrize("topics", [
    {"alerts.drift": topic()},                                             # alerts.threats does not exist
    {"alerts.drift": topic(), "alerts.threats": topic(error="unknown")},   # broker reports an error
    {"alerts.drift": topic(), "alerts.threats": topic(partitions=())},     # no partitions yet
])
def test_a_missing_alert_topic_fails_the_connection_and_closes_the_consumer(monkeypatch, topics):
    kafka = FakeKafkaClient(topics)
    monkeypatch.setitem(sys.modules, "confluent_kafka", kafka)
    with pytest.raises(RuntimeError, match="alerts.threats is not available"):
        kafka_consumer(Settings(), lambda fatal: None)
    assert kafka.created[0].closed and kafka.created[0].assigned is None
