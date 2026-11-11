from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from ace import contracts

from .runtime import EVENTS_TOPIC, Publisher, validate_task
from .utils import country_code, json_text, parse_time, utc_text

LOG = logging.getLogger(__name__)


@dataclass
class CollectionResult:
    sent: int = 0
    complete: bool = True
    failures: dict[str, str] = field(default_factory=dict)
    next_run_at: float | None = None

    def wait_until(self, when: float):
        self.complete = False
        self.next_run_at = when if self.next_run_at is None else min(self.next_run_at, when)


def failure_summary(error: Exception) -> str:
    # Never persist authentication headers or full exception request details.
    return f"HTTP {error.response.status_code}" if isinstance(error, httpx.HTTPStatusError) else type(error).__name__


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
            for table, column, definition in (
                ("pending_tasks", "attempts", "INTEGER NOT NULL DEFAULT 0"),
                ("pending_tasks", "next_run_at", "REAL NOT NULL DEFAULT 0"),
                ("pending_tasks", "expires_at", "REAL"),
                ("pending_tasks", "last_error", "TEXT"),
                ("tasks", "status", "TEXT NOT NULL DEFAULT 'complete'"),
                ("tasks", "error", "TEXT"),
            ):
                if column not in {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            self.db.execute("UPDATE pending_tasks SET expires_at=strftime('%s','now')+86400 WHERE expires_at IS NULL")
            self.db.execute("CREATE INDEX IF NOT EXISTS pending_tasks_ready ON pending_tasks(next_run_at)")
            self.db.execute("CREATE INDEX IF NOT EXISTS pending_tasks_expiry ON pending_tasks(expires_at)")

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
                self.db.execute("INSERT OR IGNORE INTO pending_tasks(task_id,payload,expires_at) VALUES (?,?,strftime('%s','now')+86400)",
                                (payload["task_id"], json_text(payload)))

    def pending_tasks(self, now: datetime | None = None, limit: int = 100):
        if now is None:
            rows = self.db.execute("SELECT payload FROM pending_tasks ORDER BY rowid")
        else:
            rows = self.db.execute("""SELECT payload FROM pending_tasks
                WHERE next_run_at <= ? OR expires_at <= ? ORDER BY next_run_at,rowid LIMIT ?""",
                (now.timestamp(), now.timestamp(), limit))
        return [json.loads(row[0]) for row in rows]

    def task_progress(self, task_id: str):
        return self.db.execute("SELECT attempts,expires_at,last_error FROM pending_tasks WHERE task_id=?", (task_id,)).fetchone()

    def defer_task(self, task_id: str, when: float, failures: dict):
        with self.db:
            self.db.execute("""UPDATE pending_tasks SET next_run_at=?, attempts=attempts+?,
                last_error=COALESCE(?,last_error) WHERE task_id=?""",
                (when, int(bool(failures)), json_text(failures) if failures else None, task_id))

    def mark_task_done(self, task_id: str, error: str | None = None):
        with self.db:
            self.db.execute("""INSERT OR IGNORE INTO tasks(task_id,completed_at,status,error)
                VALUES (?,strftime('%s','now'),?,?)""", (task_id, "failed" if error else "complete", error))
            self.db.execute("DELETE FROM pending_tasks WHERE task_id=?", (task_id,))
            self.db.execute("DELETE FROM cursors WHERE scope LIKE ?", (f"%:{task_id}",))
        if error:
            LOG.error("task=%s failed: %s", task_id, error)

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

    def collect(self, *, hours: int = 24, max_pages: int = 1, country: str | None = None,
                now: datetime | None = None, task_id: str | None = None,
                poll_seconds: int = 0, force: bool = False) -> CollectionResult:
        if not 1 <= hours <= 24 or not 1 <= max_pages <= 10 or poll_seconds < 0:
            raise ValueError("hours=1..24, max_pages=1..10, poll_seconds>=0 required")
        clock_now = (lambda: now) if now is not None else (lambda: datetime.now(timezone.utc))
        now = now or clock_now()
        utc_text(now)
        country = country_code(country) if country is not None else None
        outcome = CollectionResult(sent=self.store.drain(self.publisher, self.flush))
        for source in self.sources:
            scope = f"{source.name}:{country or '*'}:{task_id or 'scheduled'}"
            cursor = self.store.cursor(scope)
            if task_id and cursor.get("task_complete"):
                continue
            ready_at = max(self.store.feed_state(source.name)[1], cursor.get("next_poll_at", 0))
            if not force and ready_at > now.timestamp():
                outcome.wait_until(ready_at)
                continue
            if getattr(source, "snapshot_only", False) and "pending_rows" not in cursor:
                # A current blacklist cannot reconstruct yesterday's snapshot.
                # Preserve captured partial rows, but never poll for empty old windows.
                since, until = now - timedelta(hours=hours), now
                cursor = {}
            elif cursor.get("since") and cursor.get("until"):
                since, until = parse_time(cursor["since"]), parse_time(cursor["until"])
            else:
                since = parse_time(cursor["fetched_through"]) if cursor.get("fetched_through") else now - timedelta(hours=hours)
                until = min(now, since + timedelta(hours=24))
            request_cursor = {**cursor, "since": utc_text(since), "until": utc_text(until)}
            self.store.save([], scope, request_cursor)
            try:
                result = source.fetch(since, until, country=country, max_pages=max_pages, cursor=request_cursor)
            except (httpx.HTTPError, ValueError) as error:
                self.store.defer(source.name, error, clock_now())
                outcome.failures[source.name] = failure_summary(error)
                outcome.wait_until(self.store.feed_state(source.name)[1])
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
            self.store.save(result.events, scope, next_cursor)
            if result.retry_error:
                self.store.defer(source.name, result.retry_error, completed_at)
                outcome.failures[source.name] = failure_summary(result.retry_error)
            else:
                self.store.clear_failure(source.name)
            if not result.complete or result.retry_error:
                outcome.wait_until(max(next_cursor["next_poll_at"], self.store.feed_state(source.name)[1]))
            LOG.info("feed=%s observations=%d skipped=%d batch_complete=%s", source.name,
                     len(result.events), result.skipped, result.complete)
            outcome.sent += self.store.drain(self.publisher, self.flush)
        return outcome

    def accept_task(self, payload: dict, key: str | bytes | None = None):
        """Durably queue a validated task before acknowledging its Kafka input."""
        validate_task(payload, key)
        if payload["agent"] == "threat-agent":
            self.store.enqueue_task(payload)

    def _run_task(self, payload: dict, now: datetime | None = None) -> int:
        started_at = now or datetime.now(timezone.utc)
        task_id = payload["task_id"]
        progress = self.store.task_progress(task_id)
        if progress is None:
            return 0
        attempts, expires_at, last_error = progress
        if expires_at <= started_at.timestamp():
            self.store.mark_task_done(task_id, f"24-hour task deadline exceeded; {last_error or 'collection incomplete'}")
            return 0
        params = payload["params"]
        result = self.collect(hours=params.get("hours", 24), max_pages=params.get("max_pages", 1),
            country=params.get("country"), task_id=task_id, now=now)
        if result.complete and not result.failures:
            self.store.mark_task_done(task_id)
        elif attempts + int(bool(result.failures)) >= 3:
            self.store.mark_task_done(task_id, f"3 failed collection attempts: {json_text(result.failures)}")
        else:
            self.store.defer_task(task_id, result.next_run_at or started_at.timestamp() + 60, result.failures)
        return result.sent

    def run_pending_tasks(self, now: datetime | None = None) -> int:
        ready_at = now or datetime.now(timezone.utc)
        return sum(self._run_task(task, now) for task in self.store.pending_tasks(ready_at, limit=100))
