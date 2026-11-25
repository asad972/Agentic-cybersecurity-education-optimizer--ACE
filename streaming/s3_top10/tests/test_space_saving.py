"""Space-Saving on its own: error bounds, merging, ties and pickling."""

import pickle
import random
from collections import Counter
from functools import reduce

import pytest

from s3_top10.space_saving import SpaceSaving


def skewed_stream(length, distinct, seed):
    """A Zipf-like stream: item i comes about 1/i as often as item 1."""
    rng = random.Random(seed)
    items = [f"item{i:03d}" for i in range(1, distinct + 1)]
    weights = [1 / i for i in range(1, distinct + 1)]
    return rng.choices(items, weights, k=length)


def summary_of(stream, k):
    summary = SpaceSaving(k)
    for item in stream:
        summary.add(item)
    return summary


def check_bounds(summary, true):
    """Every count is an upper bound, at most error above the truth, error at most N/k."""
    assert summary.total == sum(true.values())
    for entry in summary.top():
        assert entry.guaranteed <= true[entry.item] <= entry.count
        assert entry.error <= summary.total / summary.k
    for item, count in true.items():
        if item not in summary:
            assert count <= summary.floor
    assert summary.floor <= summary.total / summary.k


def test_exact_while_counters_are_free():
    summary = summary_of(["SIEM", "IAM", "SIEM", "GDPR", "SIEM", "IAM"], k=5)
    assert [(e.item, e.count, e.error) for e in summary.top()] == [
        ("SIEM", 3, 0), ("IAM", 2, 0), ("GDPR", 1, 0)]
    assert summary.floor == 0


def test_never_underestimates_and_error_stays_within_n_over_k():
    stream = skewed_stream(5000, distinct=400, seed=1)
    summary = summary_of(stream, k=50)
    check_bounds(summary, Counter(stream))


def test_finds_the_true_top_10_of_a_skewed_stream():
    stream = skewed_stream(20000, distinct=500, seed=2)
    true = Counter(stream)
    summary = summary_of(stream, k=100)

    true_top = [item for item, _ in sorted(true.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]
    assert [e.item for e in summary.top(10)] == true_top
    # The 10 are guaranteed: each one's lower bound beats the 11th's upper bound.
    eleventh = summary.top(11)[-1]
    assert all(e.guaranteed > eleventh.count for e in summary.top(10))


def test_weighted_add_is_the_same_as_adding_one_by_one():
    one_by_one = summary_of(["a"] * 3 + ["b"] * 2 + ["c"] * 4 + ["d"], k=2)
    weighted = SpaceSaving(2)
    for item, count in [("a", 3), ("b", 2), ("c", 4), ("d", 1)]:
        weighted.add(item, count)
    assert weighted == one_by_one


def test_merge_is_as_good_as_one_summary_of_both_streams():
    first = skewed_stream(4000, distinct=300, seed=3)
    second = skewed_stream(6000, distinct=300, seed=4)
    merged = summary_of(first, 50).merge(summary_of(second, 50))

    check_bounds(merged, Counter(first + second))
    together = summary_of(first + second, 50)
    assert [e.item for e in merged.top(5)] == [e.item for e in together.top(5)]


def test_merging_30_days_keeps_the_bounds():
    days = [skewed_stream(300, distinct=200, seed=day) for day in range(30)]
    merged = reduce(SpaceSaving.merge, (summary_of(d, 20) for d in days), SpaceSaving(20))

    everything = [item for day in days for item in day]
    check_bounds(merged, Counter(everything))
    assert len(merged) == 20


def test_merge_leaves_its_inputs_unchanged():
    a, b = summary_of(["x", "y", "x"], 2), summary_of(["z", "z"], 2)
    a_before, b_before = pickle.dumps(a), pickle.dumps(b)
    a.merge(b)
    assert pickle.dumps(a) == a_before and pickle.dumps(b) == b_before


def test_merge_needs_the_same_capacity():
    with pytest.raises(ValueError):
        SpaceSaving(10).merge(SpaceSaving(20))


def test_ties_are_broken_by_item_name():
    summary = summary_of(["delta", "bravo", "charlie", "alpha"], k=10)
    assert [e.item for e in summary.top()] == ["alpha", "bravo", "charlie", "delta"]

    # When full, the tied item that ranks last ("c") makes room.
    full = summary_of(["c", "a", "b"], k=3)
    full.add("d")
    assert [(e.item, e.count, e.error) for e in full.top()] == [
        ("d", 2, 1), ("a", 1, 0), ("b", 1, 0)]


def test_same_result_whatever_the_order_of_inputs_to_merge():
    a = summary_of(skewed_stream(500, 80, seed=5), 10)
    b = summary_of(skewed_stream(500, 80, seed=6), 10)
    assert a.merge(b) == b.merge(a)


def test_pickle_round_trip():
    summary = summary_of(skewed_stream(1000, 100, seed=7), 20)
    copy = pickle.loads(pickle.dumps(summary))
    assert copy == summary
    assert copy.top() == summary.top()
    copy.add("new")
    assert copy != summary


def test_rejects_bad_arguments():
    with pytest.raises(ValueError):
        SpaceSaving(0)
    with pytest.raises(ValueError):
        SpaceSaving(5).add("x", 0)
