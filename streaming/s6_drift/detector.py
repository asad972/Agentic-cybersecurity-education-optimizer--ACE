"""Validated River ADWIN state for one regional Skill series."""

from __future__ import annotations

import math
from dataclasses import dataclass

from river import drift


@dataclass(frozen=True, slots=True)
class DriftConfig:
    """Configuration shared by every country and EU detector."""

    delta: float = 0.002
    clock: int = 1
    max_buckets: int = 5
    min_window_length: int = 5
    grace_period: int = 10
    finalization_lag_days: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.delta, bool) or not isinstance(
            self.delta, (int, float)
        ):
            raise TypeError("delta must be a number")
        if not math.isfinite(self.delta) or not 0 < self.delta < 1:
            raise ValueError("delta must be finite and in the interval (0, 1)")

        integers = {
            "clock": self.clock,
            "max_buckets": self.max_buckets,
            "min_window_length": self.min_window_length,
            "grace_period": self.grace_period,
            "finalization_lag_days": self.finalization_lag_days,
        }
        for name, value in integers.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 1:
                raise ValueError(f"{name} must be at least 1")


@dataclass(frozen=True, slots=True)
class DriftChange:
    """Deterministic metadata for one ADWIN change decision."""

    observation: float
    previous_mean: float
    new_mean: float
    width: int
    direction: str
    score: float


class ADWINSeries:
    """One picklable ADWIN detector with a stable alert-score definition."""

    def __init__(self, config: DriftConfig) -> None:
        self.config = config
        self.model = drift.ADWIN(
            delta=config.delta,
            clock=config.clock,
            max_buckets=config.max_buckets,
            min_window_length=config.min_window_length,
            grace_period=config.grace_period,
        )

    @property
    def width(self) -> int:
        return int(self.model.width)

    @property
    def mean(self) -> float:
        return float(self.model.estimation)

    def update(self, value: int | float) -> DriftChange | None:
        """Add one nonnegative daily count and return a detected change."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("demand observation must be a number")
        observation = float(value)
        if not math.isfinite(observation) or observation < 0:
            raise ValueError("demand observation must be finite and nonnegative")

        previous_mean = self.mean if self.width else observation
        self.model.update(observation)
        if not self.model.drift_detected:
            return None

        new_mean = self.mean
        direction = "up" if new_mean > previous_mean else "down"
        score = abs(new_mean - previous_mean) / max(abs(previous_mean), 1.0)
        return DriftChange(
            observation=observation,
            previous_mean=previous_mean,
            new_mean=new_mean,
            width=self.width,
            direction=direction,
            score=score,
        )
