"""Dependency helpers shared by the isolated M3 experiment workers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
from typing import Any

import numpy as np


SEED = 20261122
ROUND_DIGITS = 4
DGIM_SCENARIOS = ("stationary", "seasonal", "bursty")
ADWIN_SCENARIOS = ("stationary", "upward", "downward")
FORECAST_SCENARIOS = (
    "stable_seasonality",
    "trend_seasonality",
    "level_change",
    "intermittent",
)
MODELS = ("seasonal_naive", "sarima", "lightgbm", "production_policy")


def rng(seed: int, domain: int, scenario: int, trial: int = 0) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([seed, domain, scenario, trial]))


def rounded(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: rounded(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [rounded(item) for item in value]
    if isinstance(value, (np.floating, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("experiment metrics must be finite")
        return round(number, ROUND_DIGITS)
    if isinstance(value, np.integer):
        return int(value)
    return value


def canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(rounded(dict(value)), indent=2, sort_keys=True) + "\n"


def quantile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    return float(np.quantile(np.asarray(values, dtype=float), probability))
