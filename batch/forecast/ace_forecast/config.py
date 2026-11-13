"""Validated configuration for the forecasting job."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Mapping


SKILLS_SCOPE = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
    "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO", "GB", "CH",
)

_KEYSPACE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class ForecastConfig:
    cassandra_hosts: tuple[str, ...] = ("localhost",)
    cassandra_port: int = 9042
    cassandra_keyspace: str = "ace"
    cassandra_local_dc: str = "datacenter1"
    cassandra_username: str | None = None
    cassandra_password: str | None = None
    horizon_months: int = 12
    seasonal_naive_min_months: int = 15
    advanced_min_months: int = 27
    interval: float = 0.95

    def __post_init__(self) -> None:
        if not self.cassandra_hosts or any(not host.strip() for host in self.cassandra_hosts):
            raise ValueError("CASSANDRA_HOSTS must contain at least one hostname")
        if not 1 <= self.cassandra_port <= 65535:
            raise ValueError("CASSANDRA_PORT must be between 1 and 65535")
        if not _KEYSPACE.fullmatch(self.cassandra_keyspace):
            raise ValueError("CASSANDRA_KEYSPACE is invalid")
        if not self.cassandra_local_dc.strip():
            raise ValueError("CASSANDRA_LOCAL_DC must not be blank")
        if bool(self.cassandra_username) != bool(self.cassandra_password):
            raise ValueError("CASSANDRA_USERNAME and CASSANDRA_PASSWORD must be set together")
        if self.horizon_months != 12:
            raise ValueError("Forecast horizon must be 12 months")
        if self.seasonal_naive_min_months < 13:
            raise ValueError("Seasonal-naive history threshold must be at least 13")
        if self.advanced_min_months <= self.seasonal_naive_min_months:
            raise ValueError("Advanced history threshold must exceed fallback threshold")
        if not 0.0 < self.interval < 1.0:
            raise ValueError("Forecast interval must be between zero and one")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ForecastConfig":
        values = os.environ if env is None else env
        hosts = tuple(part.strip() for part in values.get("CASSANDRA_HOSTS", "localhost").split(","))
        try:
            port = int(values.get("CASSANDRA_PORT", "9042"))
        except ValueError as exc:
            raise ValueError("CASSANDRA_PORT must be an integer") from exc
        return cls(
            cassandra_hosts=hosts,
            cassandra_port=port,
            cassandra_keyspace=values.get("CASSANDRA_KEYSPACE", "ace"),
            cassandra_local_dc=values.get("CASSANDRA_LOCAL_DC", "datacenter1"),
            cassandra_username=values.get("CASSANDRA_USERNAME") or None,
            cassandra_password=values.get("CASSANDRA_PASSWORD") or None,
        )
