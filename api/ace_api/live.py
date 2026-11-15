"""WS /live: new drift and threat-burst alerts, pushed to browsers as they arrive.

The API reads `alerts.drift` (S6) and `alerts.threats` (S8) from Kafka, as
docs/ARCHITECTURE.md lists. Every API process follows all partitions from their
end and never joins a consumer group or commits offsets, so each replica
broadcasts every new alert and none of them takes messages away from the sink.
This is a feed of what happens from now on; history is read from Cassandra.
"""
import asyncio
from contextlib import suppress
from datetime import date, datetime
import json
import logging
import math
import re

from fastapi import WebSocket, WebSocketDisconnect

from .config import Settings

log = logging.getLogger(__name__)

ALERT_TOPICS = ("alerts.drift", "alerts.threats")
ALERT_TYPES = {"drift", "threat_burst"}
ALERT_FIELDS = ("country", "day", "created_at", "alert_type", "subject", "score", "details")
COUNTRY = re.compile(r"([A-Z]{2}|EU)")
MESSAGES = {
    "connecting": "Connecting to the alert feed.",
    "connected": "Connected. New alerts appear as they are raised.",
    "unavailable": "The alert feed is unavailable; the API keeps retrying.",
    "disabled": "The live alert feed is switched off on this API (API_LIVE_ALERTS).",
}
OVERFLOW = "Some alerts were skipped because this connection fell behind."
CLOSED = object()  # queued for a browser whose connection has ended


def parse_alert(raw) -> dict:
    """One Kafka message as the alert sent to browsers; ValueError when it does not fit shared/schemas/alert.

    S6 and S8 validate before publishing, but nothing read from the wire is forwarded unchecked.
    """
    alert = json.loads(raw)
    if not isinstance(alert, dict) or set(alert) - set(ALERT_FIELDS):
        raise ValueError("not an alert object")
    country, day, created_at, alert_type, subject, score = (alert.get(name) for name in ALERT_FIELDS[:6])
    details = alert.get("details", "")
    if not all(isinstance(value, str) for value in (country, day, created_at, subject, details)):
        raise ValueError("alert text fields must be strings")
    if not COUNTRY.fullmatch(country) or alert_type not in ALERT_TYPES or not subject:
        raise ValueError("invalid country, alert_type or subject")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise ValueError("score must be a finite number")
    date.fromisoformat(day)
    datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return {"country": country, "day": day, "created_at": created_at, "alert_type": alert_type,
            "subject": subject, "score": score, "details": details}


def kafka_consumer(settings: Settings, on_fault):
    """Blocking: a consumer positioned at the end of every alert partition.

    The Kafka client is imported here, so REST-only use and the unit tests
    (which pass their own factory) never load the native library.
    """
    from confluent_kafka import OFFSET_END, Consumer, KafkaError, TopicPartition

    def report(error):
        # The client reconnects by itself and only reports. The hub needs to know when no
        # broker is left (to tell browsers) and when the client cannot recover (to replace it).
        if error.fatal() or error.code() == KafkaError._ALL_BROKERS_DOWN:
            on_fault(error.fatal())

    consumer = Consumer({
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        # Required by the client but never joined: partitions are assigned by hand, nothing is committed.
        "group.id": "ace-api-live",
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        "error_cb": report,
    })
    try:
        partitions = []
        for topic in ALERT_TOPICS:
            metadata = consumer.list_topics(topic, timeout=5).topics.get(topic)
            if metadata is None or metadata.error is not None or not metadata.partitions:
                raise RuntimeError(f"Kafka topic {topic} is not available")
            partitions += [TopicPartition(topic, partition, OFFSET_END) for partition in metadata.partitions]
        consumer.assign(partitions)
    except Exception:
        consumer.close()
        raise
    return consumer


class AlertHub:
    """One Kafka reader per API process, fanned out to a bounded queue per browser."""

    queue_size = 100      # alerts a slow browser may fall behind before it is told it missed some
    max_clients = 200
    poll_seconds = 1.0
    heartbeat_seconds = 25.0

    def __init__(self, settings: Settings, consumer_factory=kafka_consumer):
        self.settings = settings
        self.state = "connecting" if settings.live_alerts else "disabled"
        self.skipped = 0
        self._factory = consumer_factory
        self._clients: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._consumer = None
        self._fault: str | None = None  # "down": no broker reachable; "fatal": the client must be replaced

    def status(self) -> dict:
        return {"type": "status", "state": self.state, "message": MESSAGES[self.state]}

    def _publish(self, message: dict):
        # A slow browser must never block Kafka or grow memory without bound.
        for queue in tuple(self._clients):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({**self.status(), "message": OVERFLOW})
            queue.put_nowait(message)

    def _set_state(self, state: str):
        if state != self.state:
            log.log(logging.WARNING if state == "unavailable" else logging.INFO, "Live alert feed is %s", state)
            self.state = state
            self._publish(self.status())

    def _on_fault(self, fatal: bool = False):
        # Called from the Kafka client's thread; read after each poll.
        self._fault = "fatal" if fatal or self._fault == "fatal" else "down"

    async def _wait_for_brokers(self):
        """No broker is reachable. Keep the consumer, which reconnects by itself and keeps its
        position, so alerts published around a short outage are not skipped; only report it."""
        self._set_state("unavailable")
        self._fault = None
        try:
            await self._blocking(self._consumer.list_topics, ALERT_TOPICS[0], 5)
        except Exception:
            self._fault = self._fault or "down"  # still unreachable: asked again after the next poll
        else:
            if self._fault is None:
                self._set_state("connected")

    async def _blocking(self, function, *args):
        """Run a blocking client call; if cancelled, let it finish before the consumer is closed."""
        call = asyncio.ensure_future(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(call)
        except asyncio.CancelledError:
            with suppress(Exception):
                await call
            raise

    def _connect(self):
        # Stored from inside the worker thread, so a cancelled start cannot orphan the consumer.
        self._consumer = self._factory(self.settings, self._on_fault)

    async def _run(self):
        while True:
            try:
                self._fault = None
                await self._blocking(self._connect)
                self._set_state("connected")
                while True:
                    message = await self._blocking(self._consumer.poll, self.poll_seconds)
                    if self._fault == "fatal":
                        raise RuntimeError("Fatal Kafka client error")
                    if self._fault:
                        await self._wait_for_brokers()
                    if message is None:
                        continue
                    if message.error():
                        raise RuntimeError("Kafka consumer error")
                    try:
                        alert = parse_alert(message.value())
                    except (ValueError, TypeError) as error:
                        self.skipped += 1
                        log.warning("Skipping malformed alert from %s (%s)", message.topic(), type(error).__name__)
                        continue
                    self._publish({"type": "alert", "alert": alert})
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # Broker addresses and driver text stay in the server log.
                log.warning("Live alert feed unavailable (%s: %s)", type(error).__name__, error)
                self._set_state("unavailable")
            finally:
                consumer, self._consumer = self._consumer, None
                if consumer is not None:
                    with suppress(Exception):
                        await asyncio.shield(asyncio.to_thread(consumer.close))
            await asyncio.sleep(self.settings.live_retry_seconds)

    def start(self):
        """Idempotent. Called when the first browser connects, so an unused feed never touches Kafka."""
        if self.settings.live_alerts and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._run(), name="live-alerts")

    async def stop(self):
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def serve(self, websocket: WebSocket):
        # CORS middleware does not cover WebSockets. Browsers always send Origin; other clients may omit it.
        origin = websocket.headers.get("origin")
        if origin and origin not in self.settings.cors_origins:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        if not self.settings.live_alerts:
            await websocket.send_json(self.status())
            await websocket.close(code=1000)
            return
        if len(self._clients) >= self.max_clients:
            await websocket.close(code=1013)
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.queue_size)
        self._clients.add(queue)
        self.start()

        async def receive():
            # Browsers send nothing; reading is how a closed connection is noticed.
            while (await websocket.receive())["type"] != "websocket.disconnect":
                pass

        def closed(task: asyncio.Task):
            if not task.cancelled():
                task.exception()  # retrieved, so a failed read is never logged as unhandled
            while queue.full():
                queue.get_nowait()
            queue.put_nowait(CLOSED)  # wakes the sender below

        receiver = asyncio.create_task(receive())
        receiver.add_done_callback(closed)
        try:
            await websocket.send_json(self.status())
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), self.heartbeat_seconds)
                except TimeoutError:
                    message = self.status()  # heartbeat: keeps proxies from closing a quiet feed
                if message is CLOSED:
                    return
                await websocket.send_json(message)
        except (WebSocketDisconnect, RuntimeError):
            pass  # the browser went away while a message was being sent
        finally:
            self._clients.discard(queue)
            receiver.cancel()
