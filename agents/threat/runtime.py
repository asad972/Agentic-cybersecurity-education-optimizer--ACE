"""Collector Kafka I/O using the current shared ace.contracts interface.

No dependency on the stream-stage runtime or PyFlink. Topic names are resolved
through the team's registry and all outbound messages go through contracts.route.
"""
from ace import contracts

from .utils import country_code, json_text

EVENTS_TOPIC = contracts.topic("threats.events").name
TASKS_TOPIC = contracts.topic("tasks.collect").name


class Publisher:
    def __init__(self, transport, producer="threat-agent"):
        self.transport, self.producer = transport, producer

    def publish(self, topic: str, payload: dict) -> bool:
        outgoing = contracts.route(topic, payload, producer=self.producer)
        self.transport(outgoing.topic, outgoing.key, json_text(outgoing.value).encode("utf-8"))
        return outgoing.topic == topic


class KafkaTransport:
    def __init__(self, bootstrap_servers: str, producer=None):
        if producer is None:
            from confluent_kafka import Producer
            producer = Producer({"bootstrap.servers": bootstrap_servers,
                                 "enable.idempotence": True, "acks": "all",
                                 "delivery.timeout.ms": 60000})
        self.producer = producer
        self.errors = []

    def _delivered(self, error, _message):
        if error:
            self.errors.append(str(error))

    def __call__(self, topic: str, key: str, payload: bytes):
        self.producer.produce(topic, key=key.encode("utf-8"), value=payload,
                              on_delivery=self._delivered)
        self.producer.poll(0)

    def flush(self):
        remaining = self.producer.flush(65)
        if remaining or self.errors:
            errors, self.errors = self.errors, []
            raise RuntimeError(f"Kafka delivery failed ({remaining} pending): {errors}")


def create_consumer(bootstrap_servers: str):
    from confluent_kafka import Consumer
    consumer = Consumer({"bootstrap.servers": bootstrap_servers,
                         "group.id": "ace-threat-agent",
                         "enable.auto.commit": False, "enable.auto.offset.store": False,
                         "auto.offset.reset": "earliest", "max.poll.interval.ms": 3600000})
    consumer.subscribe([TASKS_TOPIC])
    return consumer


def validate_task(payload: dict, key: str | bytes | None = None):
    contracts.validate(TASKS_TOPIC, payload)
    if key is not None:
        if isinstance(key, bytes):
            key = key.decode("utf-8")
        if key != contracts.key_of(TASKS_TOPIC, payload):
            raise contracts.InvalidMessage(TASKS_TOPIC, "Kafka key must match agent")
    if payload["agent"] == "threat-agent":
        if payload["action"] != "refresh_threats":
            raise contracts.InvalidMessage(TASKS_TOPIC, "threat-agent only supports refresh_threats")
        if "country" in payload["params"]:
            try:
                country_code(payload["params"]["country"])
            except ValueError as error:
                raise contracts.InvalidMessage(TASKS_TOPIC, str(error)) from error
