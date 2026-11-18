"""Unit tests for the River ADWIN wrapper."""

import math
import pickle

import pytest

from ..detector import ADWINSeries, DriftConfig


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"delta": 0}, ValueError),
        ({"delta": 1}, ValueError),
        ({"delta": float("nan")}, ValueError),
        ({"delta": "0.1"}, TypeError),
        ({"clock": 0}, ValueError),
        ({"clock": 1.5}, TypeError),
        ({"max_buckets": 0}, ValueError),
        ({"min_window_length": 0}, ValueError),
        ({"grace_period": 0}, ValueError),
        ({"finalization_lag_days": 0}, ValueError),
    ],
)
def test_config_rejects_invalid_values(kwargs, error) -> None:
    with pytest.raises(error):
        DriftConfig(**kwargs)


def test_series_rejects_invalid_observations() -> None:
    series = ADWINSeries(DriftConfig())

    with pytest.raises(TypeError, match="number"):
        series.update(True)
    with pytest.raises(ValueError, match="nonnegative"):
        series.update(-1)
    with pytest.raises(ValueError, match="finite"):
        series.update(math.inf)


def test_stable_series_does_not_drift() -> None:
    series = ADWINSeries(DriftConfig())

    changes = [series.update(10) for _ in range(100)]

    assert not any(changes)
    assert series.mean == 10


@pytest.mark.parametrize(
    "before,after,direction",
    [(10, 40, "up"), (40, 5, "down")],
)
def test_known_shift_reports_direction_and_relative_score(
    before: int, after: int, direction: str
) -> None:
    series = ADWINSeries(DriftConfig(delta=0.01))

    changes = []
    for value in [before] * 40 + [after] * 60:
        change = series.update(value)
        if change is not None:
            changes.append(change)

    assert changes
    change = changes[0]
    assert change.direction == direction
    assert change.observation == after
    assert change.width >= 1
    assert change.score == pytest.approx(
        abs(change.new_mean - change.previous_mean)
        / max(abs(change.previous_mean), 1)
    )


def test_series_is_pickle_compatible_and_continues_learning() -> None:
    series = ADWINSeries(DriftConfig(delta=0.01))
    for _ in range(30):
        assert series.update(10) is None

    restored = pickle.loads(pickle.dumps(series))
    changes = [restored.update(40) for _ in range(60)]

    assert any(changes)
    assert restored.width > 0
