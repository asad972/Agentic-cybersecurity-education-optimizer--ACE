"""WS /live: new drift and threat-burst alerts, pushed to browsers as they arrive.

The API reads `alerts.drift` (S6) and `alerts.threats` (S8) from Kafka, as
docs/ARCHITECTURE.md lists. Every API process follows all partitions from their
end and never joins a consumer group or commits offsets, so each replica
broadcasts every new alert and none of them takes messages away from the sink.
This is a feed of what happens from now on; history is read from Cassandra.

The API image does not install the shared `ace` package, so the topic names and
the alert check below are written out here. api/tests/test_live.py compares them
with shared/schemas/topics.csv and alert.schema.json, so they cannot drift apart.
"""
import asyncio
from contextlib import suppress
from datetime import date
import json
import logging
import math
import re
import time

from fastapi import WebSocket, WebSocketDisconnect

from .config import Settings

log = logging.getLogger(__name__)

# The topics whose schema is `alert` in shared/schemas/topics.csv.
ALERT_TOPICS = ("alerts.drift", "alerts.threats")
# shared/schemas/alert.schema.json.
ALERT_FIELDS = ("country", "day", "created_at", "alert_type", "subject", "score", "details")
REQUIRED = ALERT_FIELDS[:6]
ALERT_TYPES = {"drift", "threat_burst"}
COUNTRY = re.compile(r"([A-Z]{2}|EU)")
# The schema's date and date-time formats, checked as jsonschema checks them (RFC 3339).
DAY = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
TIMESTAMP = re.compile(r"(\d{4})-(0[1-9]|1[0-2])-(\d{2})T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d+)?"
                       r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", re.ASCII)

MESSAGES = {
    "connecting": "Connecting to the alert feed.",
    "connected": "Connected. New alerts appear as they are raised.",
    "unavailable": "The alert feed is unavailable; the API keeps retrying.",
    "disabled": "The live alert feed is switched off on this API (API_LIVE_ALERTS).",
}
OVERFLOW = "Some alerts were skipped because this connection fell behind."
SKIPPED = {"type": "skipped", "message": OVERFLOW}
REFUSED = 1008  # close code for a page whose origin is not in API_CORS_ORIGINS
CLOSED = object()  # queued for a browser whose connection has ended


def parse_alert(raw) -> dict:
    """One Kafka message as the alert sent to browsers; ValueError when it does not fit shared/schemas/alert.

    S6 and S8 validate before publishing, but nothing read from the wire is forwarded unchecked.
    """
    alert = json.loads(raw)
    if not isinstance(alert, dict) or set(alert) - set(ALERT_FIELDS) or set(REQUIRED) - set(alert):
        raise ValueError("not an alert object")
    alert = {"details": "", **alert}
    if not all(isinstance(alert[name], str) for name in ALERT_FIELDS if name != "score"):
        raise ValueError("alert text fields must be strings")
    if not COUNTRY.fullmatch(alert["country"]) or alert["alert_type"] not in ALERT_TYPES:
        raise ValueError("invalid country or alert_type")
    score = alert["score"]
    # JSON has no NaN or Infinity, although Python's json module reads them.
    if isinstance(score, bool) or not isinstance(score, (int, float)) or (isinstance(score, float) and not math.isfinite(score)):
        raise ValueError("score must be a finite number")
    if not DAY.fullmatch(alert["day"]):
        raise ValueError("day must be YYYY-MM-DD")
    date.fromisoformat(alert["day"])
    moment = TIMESTAMP.fullmatch(alert["created_at"].upper())
    if not moment:
        raise ValueError("created_at must be an RFC 3339 date-time")
    date(*map(int, moment.groups()))  # a real calendar day
    return {name: alert[name] for name in ALERT_FIELDS}


def kafka_consumer(settings: Settings, on_fault) -> "AlertConsumer":
    """A Kafka consumer for the alert topics. It follows nothing until AlertConsumer.follow() runs.

    The Kafka client is imported here, so REST-only use and the unit tests
    (which pass their own factory) never load the native library.
    """
    from confluent_kafka import Consumer, KafkaError

    def report(error):
        # The client reconnects by itself and only reports. The hub needs to know when no
        # broker is left (to tell browsers) and when the client cannot recover (to replace it).
        if error.fatal() or error.code() == KafkaError._ALL_BROKERS_DOWN:
            on_fault(error.fatal())

    return AlertConsumer(Consumer({
        "bootstrap.servers": settings.kafka_bootstrap_servers,
        # Required by the client but never joined: partitions are assigned by hand, nothing is committed.
        "group.id": "ace-api-live",
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        "error_cb": report,
    }))


class AlertConsumer:
    """The alert partitions one Kafka consumer follows.

    S6 and S8 each create their own topic, so one may not exist yet when the feed
    connects, and a topic may gain partitions later. The feed follows what exists
    and calls follow() again to pick up the rest.
    """

    def __init__(self, consumer):
        self._consumer = consumer
        self.followed: set[tuple[str, int]] = set()
        self.since: int | None = None  # ms since the epoch: when follow() first ran

    def poll(self, timeout):
        return self._consumer.poll(timeout)

    def close(self):
        self._consumer.close()

    def follow(self) -> list[str]:
        """Blocking: follow the alert partitions not followed yet; returns the alert topics that do not exist.

        Partitions found by the first call start at their end. A partition found later (a topic
        created after the feed connected, or a partition added to one) starts at the first message
        published after the first call, so nothing raised in between is skipped. Raises when no
        broker answers, which is how the hub notices that Kafka is back.
        """
        from confluent_kafka import OFFSET_END, TopicPartition

        now = int(time.time() * 1000)
        topics = self._consumer.list_topics(timeout=5).topics
        new, missing = [], []
        for name in ALERT_TOPICS:
            metadata = topics.get(name)
            if metadata is None or metadata.error is not None or not metadata.partitions:
                missing.append(name)
            else:
                new += [(name, partition) for partition in sorted(metadata.partitions) if (name, partition) not in self.followed]
        if new:
            if self.since is None:
                partitions = [TopicPartition(name, partition, OFFSET_END) for name, partition in new]
            else:
                # The first offset at or after `since` in each partition, or its end when there is none yet.
                partitions = self._consumer.offsets_for_times(
                    [TopicPartition(name, partition, self.since) for name, partition in new], timeout=5)
            self._consumer.incremental_assign(partitions)
            self.followed.update(new)
        if self.since is None:
            self.since = now
        return missing


class AlertHub:
    """One Kafka reader per API process, fanned out to a bounded queue per browser."""

    queue_size = 100      # alerts a slow browser may fall behind before it is told it missed some
    max_clients = 200
    poll_seconds = 1.0
    heartbeat_seconds = 25.0
    follow_seconds = 60.0  # how often to look for partitions added to an alert topic

    def __init__(self, settings: Settings, consumer_factory=kafka_consumer):
        self.settings = settings
        self.state = "connecting" if settings.live_alerts else "disabled"
        self.skipped = 0
        self._factory = consumer_factory
        self._clients: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._consumer = None
        self._fault: str | None = None  # "down": no broker reachable; "fatal": the client must be replaced
        self._missing: list[str] = []   # alert topics that did not exist at the last look
        self._next_follow = 0.0

    def status(self) -> dict:
        return {"type": "status", "state": self.state, "message": MESSAGES[self.state]}

    def _publish(self, message: dict):
        # A slow browser must never block Kafka or grow memory without bound.
        for queue in tuple(self._clients):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(SKIPPED)
                queue.put_nowait(self.status())  # a change of state may have been among the dropped messages
            queue.put_nowait(message)

    def _set_state(self, state: str):
        if state != self.state:
            log.log(logging.WARNING if state == "unavailable" else logging.INFO, "Live alert feed is %s", state)
            self.state = state
            self._publish(self.status())

    def _on_fault(self, fatal: bool = False):
        # Called from the Kafka client's thread; read after each poll.
        self._fault = "fatal" if fatal or self._fault == "fatal" else "down"

    def _followed(self, missing: list[str]):
        if missing and missing != self._missing:
            log.warning("Live alert feed is waiting for %s (not created yet) and looks again every %g s",
                        ", ".join(missing), self.settings.live_retry_seconds)
        elif self._missing and not missing:
            log.info("The live feed follows every alert topic")
        self._missing = missing
        self._next_follow = time.monotonic() + (self.settings.live_retry_seconds if missing else self.follow_seconds)

    async def _follow(self):
        """Look for alert topics and partitions created since the last look.

        This is also the probe while no broker is reachable. The consumer is kept: it reconnects
        by itself and keeps its position, so alerts published around a short outage are not
        skipped. Only the state is reported.
        """
        if self._fault:
            self._set_state("unavailable")
            self._fault = None
        try:
            missing = await self._blocking(self._consumer.follow)
        except Exception as error:
            log.debug("Kafka did not answer (%s)", type(error).__name__)
            self._fault = self._fault or "down"  # asked again after the next poll
            return
        if self._fault is None:
            self._set_state("connected")
        self._followed(missing)

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
        return self._consumer.follow()

    async def _run(self):
        while True:
            try:
                self._fault, self._missing = None, []
                self._followed(await self._blocking(self._connect))
                self._set_state("connected")
                while True:
                    message = await self._blocking(self._consumer.poll, self.poll_seconds)
                    if self._fault == "fatal":
                        raise RuntimeError("Fatal Kafka client error")
                    if self._fault or time.monotonic() >= self._next_follow:
                        await self._follow()
                    if message is None:
                        continue
                    if message.error():
                        raise RuntimeError("Kafka consumer error")
                    try:
                        alert = parse_alert(message.value())
                    except Exception as error:  # a bad message must never stop the feed
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
        await websocket.accept()
        # CORS middleware does not cover WebSockets. Browsers always send Origin; other clients may omit it.
        origin = websocket.headers.get("origin")
        if origin and origin not in self.settings.cors_origins:
            # Closed after the handshake, with nothing sent, so the browser sees the code: a refused
            # handshake looks like a lost connection, and the dashboard would retry it forever.
            log.warning("WS /live refused for origin %r, which is not in API_CORS_ORIGINS", origin[:200])
            await websocket.close(code=REFUSED, reason="Origin not allowed")
            return
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
