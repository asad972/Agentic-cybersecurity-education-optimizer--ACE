from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from ace import contracts

from .runtime import EVENTS_TOPIC, Publisher, validate_task
from .utils import country_code, json_text, parse_time, utc_text

LOG = logging.getLogger(__name__)


class CollectorStore:
    """Durable cursors and an outbox; downstream must deduplicate event IDs."""
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
        """)

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
            for _event_id, payload in rows:
                if not publisher.publish(EVENTS_TOPIC, json.loads(payload)):
                    raise ValueError("persisted collector observation failed validation")
            flush()  # Delivery confirmation comes before marking rows sent.
            with self.db:
                self.db.executemany("UPDATE observations SET sent=1 WHERE event_id=?", [(row[0],) for row in rows])
            sent += len(rows)
        return sent

    def task_done(self, task_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone() is not None

    def mark_task_done(self, task_id: str):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO tasks VALUES (?)", (task_id,))

    def close(self):
        self.db.close()


class ThreatCollector:
    def __init__(self, sources: list, store: CollectorStore, publisher: Publisher, flush):
        self.sources, self.store, self.publisher, self.flush = sources, store, publisher, flush

    def collect(self, *, hours: int = 24, max_pages: int = 1, country: str | None = None,
                now: datetime | None = None, task_id: str | None = None) -> int:
        if not 1 <= hours <= 24 or not 1 <= max_pages <= 10:
            raise ValueError("collection is bounded to 1–24 hours and 1–10 pages")
        now = now or datetime.now(timezone.utc)
        utc_text(now)  # Reject naive clocks instead of using the machine's timezone.
        country = country_code(country) if country is not None else None
        sent = self.store.drain(self.publisher, self.flush)
        failures = []
        for source in self.sources:
            scope = f"{source.name}:{country or '*'}:{task_id or 'scheduled'}"
            cursor = self.store.cursor(scope)
            since, until = now - timedelta(hours=hours), now
            if cursor.get("next_url"):
                since, until = parse_time(cursor["since"]), parse_time(cursor["until"])
            try:
                result = source.fetch(since, until, country=country, max_pages=max_pages, cursor=cursor)
            except (httpx.HTTPError, ValueError) as error:
                # A broken provider must not block delivery from the other feed.
                # Leave its cursor unchanged and the task unacknowledged for retry.
                LOG.error("feed=%s failed (%s)", source.name, type(error).__name__)
                failures.append(source.name)
                continue
            self.store.save(result.events, scope, result.cursor)
            LOG.info("feed=%s observations=%d skipped=%d batch_complete=%s", source.name,
                     len(result.events), result.skipped, result.complete)
            sent += self.store.drain(self.publisher, self.flush)
        if failures:
            raise RuntimeError(f"Collection failed for: {', '.join(failures)}; successful observations were delivered")
        return sent

    def handle_task(self, payload: dict, key: str | bytes | None = None) -> int:
        validate_task(payload, key)
        if payload["agent"] != "threat-agent" or payload["action"] != "refresh_threats":
            return 0
        if self.store.task_done(payload["task_id"]):
            return 0
        params = payload["params"]
        sent = self.collect(hours=params.get("hours", 24), max_pages=params.get("max_pages", 1),
            country=params.get("country"), task_id=payload["task_id"])
        # A task is one bounded collection attempt, not a promise of exhaustive
        # historical coverage. Live source quotas/paging can make it partial.
        self.store.mark_task_done(payload["task_id"])
        return sent
