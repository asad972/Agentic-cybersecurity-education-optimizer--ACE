"""The Count-Min Sketch on its own: never under-counts, stays within its bound, merges."""

import math
import os
import pickle
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from s4_demand_frequency.count_min import CountMinSketch


def zipf_stream(items, length, seed):
    """A skewed stream, like job ads: a few Skills very often, most rarely."""
    rng = random.Random(seed)
    weights = [1 / rank for rank in range(1, items + 1)]
    return rng.choices([f"item-{i}" for i in range(items)], weights, k=length)


def sketch_of(stream, **shape):
    sketch = CountMinSketch(**shape)
    for item in stream:
        sketch.add(item)
    return sketch


def test_never_under_counts():
    stream = zipf_stream(items=500, length=20_000, seed=1)
    sketch = sketch_of(stream, width=64, depth=3)   # small on purpose, so counters collide
    for item, count in Counter(stream).items():
        assert sketch.estimate(item) >= count


def test_error_within_bound():
    stream = zipf_stream(items=2000, length=50_000, seed=2)
    sketch = sketch_of(stream, width=272, depth=5)   # epsilon = 1%, delta = 0.7%
    exact = Counter(stream)
    bound = sketch.epsilon * sketch.total
    over = [item for item, count in exact.items() if sketch.estimate(item) - count > bound]
    # Each item may break the bound with probability delta; allow twice that.
    assert len(over) <= 2 * sketch.delta * len(exact)


def test_exact_when_few_items_and_wide():
    stream = zipf_stream(items=150, length=5000, seed=3)
    sketch = sketch_of(stream)
    for item, count in Counter(stream).items():
        assert sketch.estimate(item) == count


def test_unseen_item_is_zero_in_an_empty_sketch():
    assert CountMinSketch().estimate("SIEM") == 0


def test_add_with_count():
    sketch = CountMinSketch()
    sketch.add("SIEM", 3)
    sketch.add("SIEM")
    assert sketch.estimate("SIEM") == 4
    assert sketch.total == 4


def test_merge_equals_sketch_of_both_streams():
    first = zipf_stream(items=300, length=4000, seed=4)
    second = zipf_stream(items=300, length=3000, seed=5)
    merged = sketch_of(first, width=128, depth=4)
    merged.merge(sketch_of(second, width=128, depth=4))
    assert merged == sketch_of(first + second, width=128, depth=4)


def test_merge_needs_the_same_shape():
    with pytest.raises(ValueError):
        CountMinSketch(width=128).merge(CountMinSketch(width=256))
    with pytest.raises(ValueError):
        CountMinSketch(seed=1).merge(CountMinSketch(seed=2))


def test_from_error():
    sketch = CountMinSketch.from_error(epsilon=0.01, delta=0.01)
    assert (sketch.width, sketch.depth) == (272, 5)
    assert sketch.epsilon <= 0.01 and sketch.delta <= 0.01
    assert math.isclose(CountMinSketch().delta, math.exp(-5))


def test_counts_only_up():
    with pytest.raises(ValueError):
        CountMinSketch().add("SIEM", -1)


def test_pickle_round_trip():
    sketch = sketch_of(zipf_stream(items=100, length=1000, seed=6))
    copy = pickle.loads(pickle.dumps(sketch))
    assert copy == sketch
    assert copy.estimate("item-0") == sketch.estimate("item-0")


ITEMS = ["SIEM", "CISSP", "Incident response", "Cyber Incident Responder", "Ü-Prüfung"]

PRINT_CELLS = """
import sys
from s4_demand_frequency.count_min import CountMinSketch
sketch = CountMinSketch(seed=7)
print([sketch._cells(item) for item in sys.argv[1:]])
"""


def test_hashing_is_the_same_in_every_process():
    # A new process gets a new string hash seed; the sketch must not depend on it.
    streaming = str(Path(__file__).resolve().parents[2])
    runs = [
        subprocess.run(
            [sys.executable, "-c", PRINT_CELLS, *ITEMS], capture_output=True, text=True,
            check=True, cwd=streaming, env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("1", "2")
    ]
    here = CountMinSketch(seed=7)
    assert runs[0] == runs[1] == f"{[here._cells(item) for item in ITEMS]}\n"


def test_seed_changes_the_hashing():
    cells = {seed: CountMinSketch(seed=seed)._cells("SIEM") for seed in range(3)}
    assert len({tuple(c) for c in cells.values()}) == 3
