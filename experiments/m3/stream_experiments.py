"""DGIM and ADWIN experiments run in the stream dependency environment."""

from __future__ import annotations

from collections import Counter, deque
from datetime import date, timedelta
import math
from typing import Sequence

import numpy as np

from streaming.s5_supply_frequency.exponential_histogram import ExponentialHistogram
from streaming.s6_drift.detector import ADWINSeries, DriftConfig

from .common import ADWIN_SCENARIOS, DGIM_SCENARIOS, SEED, quantile, rng


def _dgim_count(name: str, day_index: int, generator: np.random.Generator) -> int:
    if name == "stationary":
        return int(generator.poisson(20.0))
    if name == "seasonal":
        rate = 20.0 + 10.0 * math.sin(2.0 * math.pi * day_index / 365.0)
        return int(generator.poisson(rate))
    if name == "bursty":
        return int(
            generator.poisson(5.0)
            + (generator.poisson(100.0) if generator.random() < 0.05 else 0)
        )
    raise ValueError(f"unknown DGIM scenario: {name}")


def _assert_histogram_invariants(histogram: ExponentialHistogram) -> None:
    buckets = histogram.buckets
    sizes = Counter(bucket.size for bucket in buckets)
    if any(size < 1 or size & (size - 1) for size in sizes):
        raise AssertionError("DGIM bucket size is not a power of two")
    if any(count > histogram.max_buckets_per_size for count in sizes.values()):
        raise AssertionError("DGIM per-size bucket limit was exceeded")
    bucket_days = [bucket.end_day for bucket in buckets]
    if bucket_days != sorted(bucket_days):
        raise AssertionError("DGIM buckets are not chronologically ordered")


def run_dgim(
    seed: int = SEED,
    *,
    trials: int = 100,
    days: int = 730,
    scenarios: Sequence[str] = DGIM_SCENARIOS,
) -> dict[str, object]:
    if trials < 1 or days < 1:
        raise ValueError("DGIM trials and days must be positive")
    results: dict[str, object] = {}
    start = date(2024, 1, 1)
    for scenario_index, scenario in enumerate(scenarios):
        relative_errors: list[float] = []
        maximum_absolute_error = 0
        maximum_buckets = 0
        maximum_compression = 0.0
        for trial in range(trials):
            generator = rng(seed, 1, scenario_index, trial)
            histogram = ExponentialHistogram(window_days=365, epsilon=0.05)
            active: deque[int] = deque()
            exact = 0
            for day_index in range(days):
                count = _dgim_count(scenario, day_index, generator)
                active.append(count)
                exact += count
                if len(active) > 365:
                    exact -= active.popleft()
                current_day = start + timedelta(days=day_index)
                if count:
                    histogram.add(current_day, count)
                estimate = histogram.estimate(current_day)
                absolute_error = abs(estimate - exact)
                if absolute_error > math.ceil(histogram.epsilon * exact):
                    raise AssertionError("DGIM exceeded its configured error bound")
                if exact == 0 and estimate != 0:
                    raise AssertionError("DGIM estimated a nonzero empty window")
                _assert_histogram_invariants(histogram)
                relative_errors.append(absolute_error / max(exact, 1))
                maximum_absolute_error = max(maximum_absolute_error, absolute_error)
                maximum_buckets = max(maximum_buckets, histogram.bucket_count)
                maximum_compression = max(
                    maximum_compression,
                    exact / max(histogram.bucket_count, 1),
                )
        results[scenario] = {
            "observations": trials * days,
            "mean_relative_error": float(np.mean(relative_errors)),
            "p95_relative_error": quantile(relative_errors, 0.95),
            "max_relative_error": max(relative_errors),
            "max_absolute_error": maximum_absolute_error,
            "max_bucket_count": maximum_buckets,
            "max_profiles_per_bucket": maximum_compression,
        }
    return {
        "configuration": {
            "window_days": 365,
            "epsilon": 0.05,
            "trials": trials,
            "days": days,
        },
        "scenarios": results,
    }


def _adwin_observation(
    name: str, day_index: int, generator: np.random.Generator
) -> int:
    if name == "stationary":
        mean = 20.0
    elif name == "upward":
        mean = 20.0 if day_index < 180 else 40.0
    elif name == "downward":
        mean = 40.0 if day_index < 180 else 20.0
    else:
        raise ValueError(f"unknown ADWIN scenario: {name}")
    return max(0, int(round(float(generator.normal(mean, 3.0)))))


def run_adwin(
    seed: int = SEED,
    *,
    trials: int = 100,
    days: int = 365,
    change_day: int = 180,
    scenarios: Sequence[str] = ADWIN_SCENARIOS,
) -> dict[str, object]:
    if trials < 1 or days < 1 or not 0 < change_day < days:
        raise ValueError("ADWIN experiment sizes are invalid")
    config = DriftConfig()
    results: dict[str, object] = {}
    for scenario_index, scenario in enumerate(scenarios):
        false_trials = 0
        total_false_alerts = 0
        detected_delays: list[int] = []
        wrong_direction_alerts = 0
        expected_direction = "up" if scenario == "upward" else "down"
        for trial in range(trials):
            generator = rng(seed, 2, scenario_index, trial)
            detector = ADWINSeries(config)
            trial_had_false_alert = False
            detected = False
            for day_index in range(days):
                change = detector.update(
                    _adwin_observation(scenario, day_index, generator)
                )
                if change is None:
                    continue
                if scenario == "stationary" or day_index < change_day:
                    total_false_alerts += 1
                    trial_had_false_alert = True
                elif change.direction == expected_direction and not detected:
                    detected_delays.append(day_index - change_day)
                    detected = True
                elif change.direction != expected_direction:
                    wrong_direction_alerts += 1
            false_trials += int(trial_had_false_alert)

        scenario_result: dict[str, object] = {
            "false_alert_trials": false_trials,
            "false_alert_rate": false_trials / trials,
            "total_false_alerts": total_false_alerts,
        }
        if scenario != "stationary":
            scenario_result.update(
                {
                    "detections": len(detected_delays),
                    "detection_rate": len(detected_delays) / trials,
                    "median_detection_delay_days": quantile(detected_delays, 0.5),
                    "p95_detection_delay_days": quantile(detected_delays, 0.95),
                    "missed_detections": trials - len(detected_delays),
                    "wrong_direction_alerts": wrong_direction_alerts,
                }
            )
        results[scenario] = scenario_result
    return {
        "configuration": {
            "trials": trials,
            "days": days,
            "change_day": change_day,
            "noise_standard_deviation": 3.0,
            "drift": {
                "delta": config.delta,
                "clock": config.clock,
                "max_buckets": config.max_buckets,
                "min_window_length": config.min_window_length,
                "grace_period": config.grace_period,
            },
        },
        "scenarios": results,
    }
