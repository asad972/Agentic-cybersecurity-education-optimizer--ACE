"""Tests for the DGIM and ADWIN experiment worker."""

from experiments.m3.common import canonical_json
from experiments.m3.stream_experiments import run_adwin, run_dgim


def test_dgim_reduced_run_is_deterministic_and_bounded() -> None:
    first = run_dgim(trials=2, days=40)
    second = run_dgim(trials=2, days=40)

    assert canonical_json(first) == canonical_json(second)
    for scenario in first["scenarios"].values():
        assert scenario["observations"] == 80
        # The worker asserts the exact ceil(epsilon * count) bound on every
        # observation. Small counts can make the relative value slightly over
        # epsilon because the aggregate contract requires integer rounding.
        assert 0 <= scenario["max_relative_error"] <= 0.1
        assert scenario["max_bucket_count"] > 0


def test_adwin_reduced_run_is_deterministic() -> None:
    first = run_adwin(trials=2)
    second = run_adwin(trials=2)

    assert canonical_json(first) == canonical_json(second)
    assert set(first["scenarios"]) == {"stationary", "upward", "downward"}
