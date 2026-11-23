"""Validated environment settings for the Skills Gap job."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

from ace.batch import CassandraSettings, SKILLS_SCOPE


@dataclass(frozen=True, slots=True)
class GapConfig(CassandraSettings):
    min_supply: int = 20
    supply_lookback_days: int = 365
    taxonomy_dir: Path = Path("shared/taxonomy")

    def __post_init__(self) -> None:
        CassandraSettings.__post_init__(self)
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
        try:
            min_supply = int(values.get("GAP_MIN_SUPPLY", "20"))
            lookback = int(values.get("GAP_SUPPLY_LOOKBACK_DAYS", "365"))
        except ValueError as exc:
            raise ValueError("Numeric Gap settings must be integers") from exc
        return cls(
            **cls._environment_fields(values),
            min_supply=min_supply,
            supply_lookback_days=lookback,
            taxonomy_dir=Path(values.get("ACE_TAXONOMY_DIR", "shared/taxonomy")),
        )
