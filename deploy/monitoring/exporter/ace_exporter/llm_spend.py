"""Today's LLM spend, read from the llm_cache table.

ace.llm stores every answer it paid for in llm_cache with its cost_eur and
created_at, and a cached answer is never paid for twice, so today's spend is
the sum of cost_eur over the rows created today. Days are UTC days.

llm_cache is keyed by (task, input_hash), not by time, so the query reads the
whole table (ALLOW FILTERING). The table holds one row per distinct LLM call,
so this is cheap enough every few minutes; the interval is a setting.

The functions here take a session object and never import the Cassandra
driver, so they are tested with a fake session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any, Callable, Iterable

QUERY = "SELECT task, provider, cost_eur, created_at FROM llm_cache WHERE created_at >= %s ALLOW FILTERING"

# Driver errors that mean Cassandra itself cannot be reached during a query.
CONNECTION_ERRORS = frozenset({"NoHostAvailable", "OperationTimedOut", "ConnectionException"})

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Spend:
    eur: float = 0.0
    calls: int = 0


@dataclass(frozen=True)
class SpendState:
    """The latest result of the spend check, as shown on the panels."""

    cassandra_up: bool = False          # connected to Cassandra and the keyspace
    available: bool = False             # llm_cache could be read
    by_task: dict[tuple[str, str], Spend] = field(default_factory=dict)   # (task, provider)
    checked_at: datetime | None = None


def day_start(now: datetime) -> datetime:
    """Midnight UTC of the day now falls on."""
    now = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return datetime.combine(now.date(), time.min, timezone.utc)


def _utc(moment: datetime) -> datetime:
    # The driver returns timestamps as naive datetimes in UTC.
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def _label(value: Any) -> str:
    return value if isinstance(value, str) and value else "unknown"


def spend_by_task(rows: Iterable[Any], since: datetime) -> dict[tuple[str, str], Spend]:
    """Sum cost_eur and count calls per (task, provider) over rows created at or after since.

    rows have task, provider, cost_eur and created_at attributes, like the
    driver's named-tuple rows. A missing cost (e.g. from the free local model)
    counts as 0 EUR.
    """
    totals: dict[tuple[str, str], Spend] = {}
    for row in rows:
        if row.created_at is None or _utc(row.created_at) < since:
            continue
        key = (_label(row.task), _label(row.provider))
        old = totals.get(key, Spend())
        totals[key] = Spend(old.eur + float(row.cost_eur or 0.0), old.calls + 1)
    return totals


def check(connect: Callable[[], Any], now: datetime) -> SpendState:
    """Read today's spend; never raises, so a missing table or a Cassandra outage only shows as 0 on the panels.

    connect() returns a Cassandra session for the ace keyspace (and may raise).
    """
    try:
        session = connect()
    except Exception as error:  # noqa: BLE001 - any connection problem means "Cassandra down"
        log.warning("cannot connect to Cassandra: %s: %s", type(error).__name__, error)
        return SpendState(checked_at=now)
    since = day_start(now)
    try:
        rows = session.execute(QUERY, (since,))
        return SpendState(True, True, spend_by_task(rows, since), now)
    except Exception as error:  # noqa: BLE001 - e.g. llm_cache not created yet
        log.warning("cannot read llm_cache: %s: %s", type(error).__name__, error)
        # Matched by name so this module never imports the driver.
        reachable = type(error).__name__ not in CONNECTION_ERRORS
        return SpendState(reachable, False, {}, now)
