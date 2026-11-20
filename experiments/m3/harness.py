"""Standard-library orchestrator for isolated M3 experiment workers."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
import subprocess
import sys


SEED = 20261122
DGIM_SCENARIOS = ("stationary", "seasonal", "bursty")
ADWIN_SCENARIOS = ("stationary", "upward", "downward")
FORECAST_SCENARIOS = (
    "stable_seasonality",
    "trend_seasonality",
    "level_change",
    "intermittent",
)
MODELS = ("seasonal_naive", "sarima", "lightgbm", "production_policy")


def canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(dict(value), indent=2, sort_keys=True) + "\n"


def _worker(kind: str, seed: int) -> dict[str, object]:
    variable = "ACE_STREAM_PYTHON" if kind == "stream" else "ACE_FORECAST_PYTHON"
    interpreter = os.environ.get(variable, sys.executable)
    result = subprocess.run(
        [interpreter, "-m", "experiments.m3.worker", kind, "--seed", str(seed)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = (
            result.stderr.strip().splitlines()[-1]
            if result.stderr.strip()
            else "unknown error"
        )
        raise RuntimeError(f"{kind} experiment worker failed: {detail}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{kind} experiment worker returned invalid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{kind} experiment worker returned an invalid object")
    return value


def build_results(revision: str, seed: int = SEED) -> dict[str, object]:
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("revision must be a non-empty string")
    stream = _worker("stream", seed)
    forecast = _worker("forecast", seed)
    return {
        "schema_version": 1,
        "revision": revision.strip(),
        "seed": seed,
        "environment": {
            "isolation": "separate stream and forecast virtual environments",
            "stream_python": stream["python"],
            "stream_packages": stream["packages"],
            "forecast_python": forecast["python"],
            "forecast_packages": forecast["packages"],
        },
        "experiments": {
            **stream["experiments"],
            **forecast["experiments"],
        },
    }


def _format(value: object, *, percent: bool = False) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    if percent:
        return f"{number * 100:.2f}%"
    return f"{number:.4f}".rstrip("0").rstrip(".")


def render_table(results: Mapping[str, object]) -> str:
    experiments = results.get("experiments")
    if not isinstance(experiments, Mapping):
        raise ValueError("results are missing experiments")
    lines = [
        "| Component | Scenario | Measured result |",
        "| --- | --- | --- |",
    ]
    dgim = experiments["s5_dgim"]["scenarios"]
    for name in DGIM_SCENARIOS:
        item = dgim[name]
        lines.append(
            f"| S5 DGIM | {name.replace('_', ' ').title()} | "
            f"relative error mean/p95/max {_format(item['mean_relative_error'], percent=True)} / "
            f"{_format(item['p95_relative_error'], percent=True)} / "
            f"{_format(item['max_relative_error'], percent=True)}; "
            f"max {item['max_bucket_count']} buckets |"
        )
    adwin = experiments["s6_adwin"]["scenarios"]
    stationary = adwin["stationary"]
    lines.append(
        f"| S6 ADWIN | Stationary | false-alert trials {stationary['false_alert_trials']}/100 "
        f"({_format(stationary['false_alert_rate'], percent=True)}) |"
    )
    for name in ("upward", "downward"):
        item = adwin[name]
        lines.append(
            f"| S6 ADWIN | {name.title()} shift | detection {_format(item['detection_rate'], percent=True)}; "
            f"median/p95 delay {_format(item['median_detection_delay_days'])} / "
            f"{_format(item['p95_detection_delay_days'])} days |"
        )
    forecast = experiments["forecast"]["scenarios"]
    for name in FORECAST_SCENARIOS:
        item = forecast[name]
        models = item["models"]
        mae = "/".join(_format(models[model]["mae"]) for model in MODELS)
        selections = ", ".join(
            f"{model}={count}"
            for model, count in item["production_policy_selections"].items()
        )
        lines.append(
            f"| Forecast | {name.replace('_', ' ').title()} | MAE SN/SARIMA/LightGBM/policy "
            f"{mae}; policy coverage {_format(models['production_policy']['interval_coverage'], percent=True)}; "
            f"selected {selections} |"
        )
    return "\n".join(lines)
