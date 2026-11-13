"""Validated environment settings for the Skills Gap job."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Mapping


SKILLS_SCOPE = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
    "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO", "GB", "CH",
)

_KEYSPACE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class GapConfig:
    cassandra_hosts: tuple[str, ...] = ("localhost",)
    cassandra_port: int = 9042
    cassandra_keyspace: str = "ace"
    cassandra_local_dc: str = "datacenter1"
    cassandra_username: str | None = None
    cassandra_password: str | None = None
    min_supply: int = 20
    supply_lookback_days: int = 365
    taxonomy_dir: Path = Path("shared/taxonomy")

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
        if isinstance(self.min_supply, bool) or not isinstance(self.min_supply, int) or self.min_supply <= 0:
            raise ValueError("GAP_MIN_SUPPLY must be a positive integer")
        if (
            isinstance(self.supply_lookback_days, bool)
            or not isinstance(self.supply_lookback_days, int)
            or not 1 <= self.supply_lookback_days <= 365
        ):
            raise ValueError("GAP_SUPPLY_LOOKBACK_DAYS must be between 1 and 365")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "GapConfig":
        values = os.environ if env is None else env
        hosts = tuple(part.strip() for part in values.get("CASSANDRA_HOSTS", "localhost").split(","))
        try:
            port = int(values.get("CASSANDRA_PORT", "9042"))
            min_supply = int(values.get("GAP_MIN_SUPPLY", "20"))
            lookback = int(values.get("GAP_SUPPLY_LOOKBACK_DAYS", "365"))
        except ValueError as exc:
            raise ValueError("Numeric Gap and Cassandra settings must be integers") from exc
        return cls(
            cassandra_hosts=hosts,
            cassandra_port=port,
            cassandra_keyspace=values.get("CASSANDRA_KEYSPACE", "ace"),
            cassandra_local_dc=values.get("CASSANDRA_LOCAL_DC", "datacenter1"),
            cassandra_username=values.get("CASSANDRA_USERNAME") or None,
            cassandra_password=values.get("CASSANDRA_PASSWORD") or None,
            min_supply=min_supply,
            supply_lookback_days=lookback,
            taxonomy_dir=Path(values.get("ACE_TAXONOMY_DIR", "shared/taxonomy")),
        )
