"""Unit tests for the DGIM histogram and per-country Supply counter."""

import math
import pickle
from collections import Counter
from datetime import date, datetime

import pytest

from ..exponential_histogram import ExponentialHistogram, SupplyCounter


def test_is_exact_before_bucket_compression() -> None:
    histogram = ExponentialHistogram(epsilon=0.05)
    for offset in range(8):
        histogram.add(date(2026, 1, 1 + offset))

    assert histogram.estimate("2026-01-08") == 8
    assert [bucket.size for bucket in histogram.buckets] == [1] * 8


def test_buckets_are_powers_of_two_and_respect_the_per_size_limit() -> None:
    histogram = ExponentialHistogram(epsilon=0.5)
    histogram.add("2026-01-01", 100)

    sizes = [bucket.size for bucket in histogram.buckets]
    assert all(size & (size - 1) == 0 for size in sizes)
    assert all(
        count <= histogram.max_buckets_per_size
        for count in Counter(sizes).values()
    )
    assert any(size >= 16 for size in sizes)  # cascading merges occurred


@pytest.mark.parametrize("value", [1, 10, 11, 12, 37, 1_000])
def test_positive_integer_add_matches_repeated_insertions(value: int) -> None:
    batched = ExponentialHistogram(epsilon=0.2)
    repeated = ExponentialHistogram(epsilon=0.2)

    batched.add("2026-01-01", value)
    for _ in range(value):
        repeated.add("2026-01-01")

    assert batched.buckets == repeated.buckets
    assert batched.estimate("2026-01-01") == repeated.estimate("2026-01-01")


def test_batched_add_matches_repeated_insertions_with_existing_state() -> None:
    batched = ExponentialHistogram(epsilon=0.2)
    repeated = ExponentialHistogram(epsilon=0.2)

    for day, value in [("2026-01-01", 37), ("2026-01-02", 1_000)]:
        batched.add(day, value)
        for _ in range(value):
            repeated.add(day)

    assert batched.buckets == repeated.buckets


def test_large_batch_has_bounded_buckets_and_configured_error() -> None:
    value = 1_000_000
    histogram = ExponentialHistogram(epsilon=0.05)

    histogram.add("2026-01-01", value)

    sizes = [bucket.size for bucket in histogram.buckets]
    assert sum(sizes) == value
    assert histogram.bucket_count <= (
        histogram.max_buckets_per_size * value.bit_length()
    )
    assert all(
        count <= histogram.max_buckets_per_size
        for count in Counter(sizes).values()
    )
    estimate = histogram.estimate("2026-01-01")
    assert abs(estimate - value) <= math.ceil(value * histogram.epsilon)


def test_window_includes_day_minus_364_and_expires_day_minus_365() -> None:
    histogram = ExponentialHistogram(window_days=365)
    histogram.add("2025-01-01")
    histogram.add("2025-01-02")

    assert histogram.estimate("2026-01-01") == 1
    assert histogram.buckets[0].end_day == date(2025, 1, 2)


def test_estimate_stays_within_configured_error_allowing_integer_rounding() -> None:
    histogram = ExponentialHistogram(window_days=365, epsilon=0.05)
    exact = 0
    start = date(2025, 1, 1)
    for offset in range(500):
        day = start.fromordinal(start.toordinal() + offset)
        value = offset % 5 + 1
        histogram.add(day, value)
        if offset >= 500 - 365:
            exact += value

    estimate = histogram.estimate(start.fromordinal(start.toordinal() + 499))
    assert abs(estimate - exact) <= math.ceil(exact * histogram.epsilon)


def test_empty_histogram_and_complete_expiration_return_zero() -> None:
    empty = ExponentialHistogram()
    assert empty.estimate("2026-01-01") == 0

    histogram = ExponentialHistogram(window_days=2)
    histogram.add("2026-01-01", 4)
    assert histogram.estimate("2026-01-03") == 0
    assert histogram.bucket_count == 0


@pytest.mark.parametrize(
    "kwargs,error",
    [
        ({"window_days": 0}, ValueError),
        ({"window_days": 1.5}, TypeError),
        ({"epsilon": 0}, ValueError),
        ({"epsilon": 0.75}, ValueError),
        ({"epsilon": float("nan")}, ValueError),
    ],
)
def test_rejects_invalid_configuration(kwargs, error) -> None:
    with pytest.raises(error):
        ExponentialHistogram(**kwargs)


def test_rejects_invalid_values_dates_and_time_reversal() -> None:
    histogram = ExponentialHistogram()
    with pytest.raises(ValueError, match="at least 1"):
        histogram.add("2026-01-01", 0)
    with pytest.raises(TypeError, match="integer"):
        histogram.add("2026-01-01", 1.5)
    with pytest.raises(ValueError, match="without a time"):
        histogram.add(datetime(2026, 1, 1, 12, 0))

    histogram.add("2026-01-02")
    with pytest.raises(ValueError, match="nondecreasing"):
        histogram.add("2026-01-01")
    with pytest.raises(ValueError, match="nondecreasing"):
        histogram.estimate("2026-01-01")


def test_histogram_and_counter_are_pickle_compatible() -> None:
    counter = SupplyCounter(epsilon=0.1)
    counter.add_daily_counts("2026-01-01", {"SIEM": 3, "Linux": 1})

    restored = pickle.loads(pickle.dumps(counter))

    assert restored.emit("DE", "2026-01-01") == counter.emit(
        "DE", "2026-01-01"
    )
    assert restored.last_day == date(2026, 1, 1)


def test_supply_counter_sorts_skills_and_prunes_expired_histograms() -> None:
    counter = SupplyCounter(window_days=2, epsilon=0.05)
    counter.add_daily_counts("2026-01-01", {"SIEM": 2, "Linux": 1})
    first = counter.emit("DE", "2026-01-01")

    assert [item["item"] for item in first["counts"]] == ["Linux", "SIEM"]

    counter.add_daily_counts("2026-01-03", {"Cloud Security": 1})
    third = counter.emit("DE", "2026-01-03")
    assert third["counts"] == [
        {"kind": "skill", "item": "Cloud Security", "count": 1}
    ]
    assert counter.active_skills == ("Cloud Security",)


def test_supply_counter_validates_country_and_daily_counts() -> None:
    counter = SupplyCounter()
    with pytest.raises(ValueError, match="country"):
        counter.emit("Germany", "2026-01-01")
    with pytest.raises(ValueError, match="non-empty"):
        counter.add_daily_counts("2026-01-01", {"": 1})
    with pytest.raises(ValueError, match="positive"):
        counter.add_daily_counts("2026-01-01", {"SIEM": 0})
