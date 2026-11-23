"""Tests for the dependency-isolated experiment orchestrator."""

from __future__ import annotations

from copy import deepcopy

from experiments.m3 import harness


def _sample_results() -> dict[str, object]:
    return {
        "experiments": {
            "s5_dgim": {
                "scenarios": {
                    name: {
                        "mean_relative_error": 0.01,
                        "p95_relative_error": 0.02,
                        "max_relative_error": 0.03,
                        "max_bucket_count": 4,
                    }
                    for name in harness.DGIM_SCENARIOS
                }
            },
            "s6_adwin": {
                "scenarios": {
                    "stationary": {
                        "false_alert_trials": 1,
                        "false_alert_rate": 0.01,
                    },
                    "upward": {
                        "detection_rate": 1.0,
                        "median_detection_delay_days": 3,
                        "p95_detection_delay_days": 5,
                    },
                    "downward": {
                        "detection_rate": 0.9,
                        "median_detection_delay_days": 4,
                        "p95_detection_delay_days": 7,
                    },
                }
            },
            "forecast": {
                "scenarios": {
                    name: {
                        "models": {
                            model: {"mae": 1.0, "interval_coverage": 0.95}
                            for model in harness.MODELS
                        },
                        "production_policy_selections": {"sarima": 3},
                    }
                    for name in harness.FORECAST_SCENARIOS
                }
            },
        }
    }


def test_table_uses_all_experiment_sections() -> None:
    table = harness.render_table(_sample_results())

    assert "S5 DGIM" in table
    assert "S6 ADWIN" in table
    assert "Forecast" in table
    assert "SN/SARIMA/LightGBM/policy" in table


def test_build_results_combines_isolated_workers(monkeypatch) -> None:
    def worker(kind: str, seed: int) -> dict[str, object]:
        return {
            "python": "3.11.16",
            "packages": {kind: "1.0"},
            "experiments": {kind: {"seed": seed}},
        }

    monkeypatch.setattr(harness, "_worker", worker)

    result = harness.build_results("abc123")

    assert result["revision"] == "abc123"
    assert result["seed"] == harness.SEED
    assert result["experiments"] == {
        "stream": {"seed": harness.SEED},
        "forecast": {"seed": harness.SEED},
    }
    assert "separate" in result["environment"]["isolation"]


def test_canonical_json_detects_metric_tampering() -> None:
    original = {"schema_version": 1, "value": 1.2346}
    changed = deepcopy(original)
    changed["value"] = 2.0

    assert harness.canonical_json(original) != harness.canonical_json(changed)
