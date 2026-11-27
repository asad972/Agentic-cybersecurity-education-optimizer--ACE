"""Shared infrastructure for ACE batch jobs.

The module intentionally imports the Cassandra driver only when a connection is
opened. Services that use the country scope or calendar helpers therefore keep
the lightweight base ``ace`` installation.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
import os
import re
from typing import Any, Self


# Skills Scope: EU27, EEA countries outside the EU, the UK and Switzerland.
SKILLS_SCOPE = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
    "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO", "GB", "CH",
)

_KEYSPACE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass(frozen=True, slots=True)
class CassandraSettings:
    """Validated Cassandra settings shared by the batch jobs."""

    cassandra_hosts: tuple[str, ...] = ("localhost",)
    cassandra_port: int = 9042
    cassandra_keyspace: str = "ace"
    cassandra_local_dc: str = "datacenter1"
    cassandra_username: str | None = None
    cassandra_password: str | None = None

    def __post_init__(self) -> None:
        if not self.cassandra_hosts or any(
            not host.strip() for host in self.cassandra_hosts
        ):
            raise ValueError("CASSANDRA_HOSTS must contain at least one hostname")
        if not 1 <= self.cassandra_port <= 65535:
            raise ValueError("CASSANDRA_PORT must be between 1 and 65535")
        if not _KEYSPACE.fullmatch(self.cassandra_keyspace):
            raise ValueError("CASSANDRA_KEYSPACE is invalid")
        if not self.cassandra_local_dc.strip():
            raise ValueError("CASSANDRA_LOCAL_DC must not be blank")
        if bool(self.cassandra_username) != bool(self.cassandra_password):
            raise ValueError(
                "CASSANDRA_USERNAME and CASSANDRA_PASSWORD must be set together"
            )

    @classmethod
    def _environment_fields(
        cls, env: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        values = os.environ if env is None else env
        hosts = tuple(
            part.strip()
            for part in values.get("CASSANDRA_HOSTS", "localhost").split(",")
        )
        try:
            port = int(values.get("CASSANDRA_PORT", "9042"))
        except ValueError as exc:
            raise ValueError("CASSANDRA_PORT must be an integer") from exc
        return {
            "cassandra_hosts": hosts,
            "cassandra_port": port,
            "cassandra_keyspace": values.get("CASSANDRA_KEYSPACE", "ace"),
            "cassandra_local_dc": values.get(
                "CASSANDRA_LOCAL_DC", "datacenter1"
            ),
            "cassandra_username": values.get("CASSANDRA_USERNAME") or None,
            "cassandra_password": values.get("CASSANDRA_PASSWORD") or None,
        }

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        return cls(**cls._environment_fields(env))


class CassandraRepositoryBase:
    """Shared Cassandra connection and context-manager lifecycle."""

    def __init__(self, session: Any, cluster: Any = None) -> None:
        self._session = session
        self._cluster = cluster

    @classmethod
    def connect(cls, config: CassandraSettings) -> Self:
        from cassandra.auth import PlainTextAuthProvider
        from cassandra.cluster import Cluster
        from cassandra.policies import DCAwareRoundRobinPolicy

        auth = None
        if config.cassandra_username is not None:
            auth = PlainTextAuthProvider(
                config.cassandra_username, config.cassandra_password or ""
            )
        cluster = Cluster(
            list(config.cassandra_hosts),
            port=config.cassandra_port,
            protocol_version=4,
            load_balancing_policy=DCAwareRoundRobinPolicy(
                local_dc=config.cassandra_local_dc
            ),
            auth_provider=auth,
        )
        try:
            session = cluster.connect(config.cassandra_keyspace)
            return cls(session, cluster)
        except Exception:
            cluster.shutdown()
            raise

    def close(self) -> None:
        if self._cluster is not None:
            cluster, self._cluster = self._cluster, None
            cluster.shutdown()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def month_start(value: date) -> date:
    """Return the first day of ``value``'s calendar month."""
    return date(value.year, value.month, 1)


def add_months(value: date, count: int) -> date:
    """Move a month-start date by ``count`` calendar months."""
    index = value.year * 12 + value.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


@dataclass(frozen=True, slots=True)
class TargetMonth:
    """A validated complete calendar month used by monthly jobs."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.year <= 9999 or not 1 <= self.month <= 12:
            raise ValueError("Target month must be a valid YYYY-MM")

    @classmethod
    def parse(cls, value: str) -> TargetMonth:
        match = _MONTH.fullmatch(value)
        if not match:
            raise ValueError("Target month must use YYYY-MM")
        return cls(int(match.group(1)), int(match.group(2)))

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        return date(
            self.year,
            self.month,
            calendar.monthrange(self.year, self.month)[1],
        )

    def days(self) -> tuple[date, ...]:
        return tuple(
            self.first_day + timedelta(days=offset)
            for offset in range((self.last_day - self.first_day).days + 1)
        )


def previous_complete_month(today: date) -> TargetMonth:
    """Return the complete calendar month immediately before ``today``."""
    previous_day = today.replace(day=1) - timedelta(days=1)
    return TargetMonth(previous_day.year, previous_day.month)
