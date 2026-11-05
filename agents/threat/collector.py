from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from ace import contracts

from .runtime import EVENTS_TOPIC, Publisher, validate_task
from .utils import country_code, json_text, parse_time, utc_text

LOG = logging.getLogger(__name__)


def retry_delay(error: Exception, failures: int, now: datetime) -> float:
    """Honor provider reset times; persist a conservative delay if none is given."""
    delay = min(60 * 2 ** min(max(failures - 1, 0), 6), 3600)
    if isinstance(error, httpx.HTTPStatusError):
        headers = error.response.headers
        if error.response.status_code in {401, 403}:
            delay = 3600
        if error.response.status_code == 429:
            delay = 21600
        hints = []
        value = headers.get("Retry-After")
        if value:
            try:
                hints.append(float(value))
            except ValueError:
                try:
                    hints.append((parsedate_to_datetime(value) - now).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        if error.response.status_code == 429:
            try:
                hints.append(float(headers["X-RateLimit-Reset"]) - now.timestamp())
            except (KeyError, ValueError):
                pass
        future_hints = [hint for hint in hints if math.isfinite(hint) and 0 < hint <= 365 * 86400]
        if future_hints:
            delay = max(60, *future_hints)
    return delay


class CollectorStore:
    """SQLite outbox, resumable tasks and feed cooldowns shared across restarts."""
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS observations (
                event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS cursors (scope TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS pending_tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feed_state (
                source TEXT PRIMARY KEY, failures INTEGER NOT NULL, retry_at REAL NOT NULL);
        """)
        # In-place migration of the state created by the original collector.
        with self.db:
            for table, column in (("observations", "handled_at"), ("tasks", "completed_at")):
                if column not in {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} REAL")
            self.db.execute("UPDATE observations SET handled_at=strftime('%s','now') WHERE sent=1 AND handled_at IS NULL")
            self.db.execute("UPDATE tasks SET completed_at=strftime('%s','now') WHERE completed_at IS NULL")
            self.db.execute("CREATE INDEX IF NOT EXISTS observations_pending ON observations(sent, handled_at)")

    def cursor(self, scope: str):
        row = self.db.execute("SELECT value FROM cursors WHERE scope=?", (scope,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save(self, events: list[dict], scope: str | None = None, cursor: dict | None = None):
        for row in events:
            contracts.validate(EVENTS_TOPIC, row)
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO observations(event_id,payload) VALUES (?,?)",
                [(row["event_id"], json_text(row)) for row in events])
            if scope is not None:
                self.db.execute("INSERT OR REPLACE INTO cursors VALUES (?,?)", (scope, json_text(cursor or {})))

    def drain(self, publisher: Publisher, flush, batch_size: int = 100) -> int:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        sent = 0
        while rows := self.db.execute("SELECT event_id,payload FROM observations WHERE sent=0 ORDER BY rowid LIMIT ?",
                                     (batch_size,)).fetchall():
            for event_id, payload in rows:
                try:
                    record = json.loads(payload)
                except ValueError:
                    record = contracts.invalid_record(EVENTS_TOPIC, "threat-agent", "outbox payload is not JSON", payload)
                    publisher.publish(contracts.INVALID_TOPIC, record)
                else:
                    if not publisher.publish(EVENTS_TOPIC, record):
                        LOG.warning("Outbox event %s routed to errors.invalid", event_id)
            flush()  # Confirm events AND rejects before retiring any outbox rows.
            with self.db:
                self.db.executemany("UPDATE observations SET sent=1, handled_at=strftime('%s','now') WHERE event_id=?",
                                    [(row[0],) for row in rows])
            sent += len(rows)
        return sent

    def feed_state(self, source: str):
        row = self.db.execute("SELECT failures,retry_at FROM feed_state WHERE source=?", (source,)).fetchone()
        return row or (0, 0.0)

    def defer(self, source: str, error: Exception, now: datetime):
        failures = self.feed_state(source)[0] + 1
        retry_at = now.timestamp() + retry_delay(error, failures, now)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO feed_state VALUES (?,?,?)", (source, failures, retry_at))
        LOG.warning("feed=%s deferred until %s (%s)", source,
                    utc_text(datetime.fromtimestamp(retry_at, timezone.utc)), type(error).__name__)

    def clear_failure(self, source: str):
        with self.db:
            self.db.execute("DELETE FROM feed_state WHERE source=?", (source,))

    def task_done(self, task_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone() is not None

    def enqueue_task(self, payload: dict):
        if not self.task_done(payload["task_id"]):
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO pending_tasks VALUES (?,?)",
                                (payload["task_id"], json_text(payload)))

    def pending_tasks(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM pending_tasks ORDER BY rowid")]

    def mark_task_done(self, task_id: str):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO tasks(task_id,completed_at) VALUES (?,strftime('%s','now'))", (task_id,))
            self.db.execute("DELETE FROM pending_tasks WHERE task_id=?", (task_id,))
            self.db.execute("DELETE FROM cursors WHERE scope LIKE ?", (f"%:{task_id}",))

    def prune(self, retention_days: int = 30, now: datetime | None = None):
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        cutoff = (now or datetime.now(timezone.utc)).timestamp() - retention_days * 86400
        with self.db:
            self.db.execute("DELETE FROM observations WHERE sent=1 AND handled_at < ?", (cutoff,))
            self.db.execute("DELETE FROM tasks WHERE completed_at < ?", (cutoff,))

    def close(self):
        self.db.close()


class ThreatCollector:
    def __init__(self, sources: list, store: CollectorStore, publisher: Publisher, flush):
        self.sources, self.store, self.publisher, self.flush = sources, store, publisher, flush
        self.last_complete = True

    def collect(self, *, hours: int = 24, max_pages: int = 1, country: str | None = None,
                now: datetime | None = None, task_id: str | None = None,
                poll_seconds: int = 0) -> int:
        if not 1 <= hours <= 24 or not 1 <= max_pages <= 10 or poll_seconds < 0:
            raise ValueError("hours=1..24, max_pages=1..10, poll_seconds>=0 required")
        clock_now = (lambda: now) if now is not None else (lambda: datetime.now(timezone.utc))
        now = now or clock_now()
        utc_text(now)
        country = country_code(country) if country is not None else None
        sent = self.store.drain(self.publisher, self.flush)
        self.last_complete = True
        for source in self.sources:
            scope = f"{source.name}:{country or '*'}:{task_id or 'scheduled'}"
            cursor = self.store.cursor(scope)
            if task_id and cursor.get("task_complete"):
                continue
            if cursor.get("since") and cursor.get("until"):
                since, until = parse_time(cursor["since"]), parse_time(cursor["until"])
            else:
                since = parse_time(cursor["fetched_through"]) if cursor.get("fetched_through") else now - timedelta(hours=hours)
                until = min(now, since + timedelta(hours=24))
            if self.store.feed_state(source.name)[1] > now.timestamp() or cursor.get("next_poll_at", 0) > now.timestamp():
                if task_id or cursor.get("until"):
                    self.last_complete = False
                continue
            # Freeze the window even when the initial provider request fails.
            request_cursor = {**cursor, "since": utc_text(since), "until": utc_text(until)}
            self.store.save([], scope, request_cursor)
            try:
                result = source.fetch(since, until, country=country, max_pages=max_pages, cursor=request_cursor)
            except (httpx.HTTPError, ValueError) as error:
                self.store.defer(source.name, error, clock_now())
                self.last_complete = False
                continue
            next_cursor = dict(result.cursor)
            completed_at = clock_now()
            if result.complete:
                next_cursor["fetched_through"] = utc_text(until)
                next_cursor["next_poll_at"] = completed_at.timestamp() + (min(60, poll_seconds) if until < now else poll_seconds)
                if task_id:
                    next_cursor["task_complete"] = True
            else:
                next_cursor.update(since=utc_text(since), until=utc_text(until))
                next_cursor["next_poll_at"] = completed_at.timestamp() + 60
                self.last_complete = False
            # Enqueue successful rows and advance past them in one transaction.
            self.store.save(result.events, scope, next_cursor)
            if result.retry_error:
                self.store.defer(source.name, result.retry_error, completed_at)
            else:
                self.store.clear_failure(source.name)
            LOG.info("feed=%s observations=%d skipped=%d batch_complete=%s", source.name,
                     len(result.events), result.skipped, result.complete)
            sent += self.store.drain(self.publisher, self.flush)
        return sent

    def accept_task(self, payload: dict, key: str | bytes | None = None):
        """Durably queue a validated task before acknowledging its Kafka input."""
        validate_task(payload, key)
        if payload["agent"] == "threat-agent":
            self.store.enqueue_task(payload)

    def _run_task(self, payload: dict, now: datetime | None = None) -> int:
        if self.store.task_done(payload["task_id"]):
            return 0
        params = payload["params"]
        sent = self.collect(hours=params.get("hours", 24), max_pages=params.get("max_pages", 1),
            country=params.get("country"), task_id=payload["task_id"], now=now)
        if self.last_complete:
            self.store.mark_task_done(payload["task_id"])
        return sent

    def handle_task(self, payload: dict, key: str | bytes | None = None) -> int:
        self.accept_task(payload, key)
        return self._run_task(payload) if payload["agent"] == "threat-agent" else 0

    def run_pending_tasks(self, now: datetime | None = None) -> int:
        return sum(self._run_task(task, now) for task in self.store.pending_tasks())
