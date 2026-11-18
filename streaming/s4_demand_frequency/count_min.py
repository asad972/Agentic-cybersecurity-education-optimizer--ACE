"""Count-Min Sketch: approximate counts of many items in a fixed, small memory.

The sketch is a table of depth rows by width counters. Adding an item adds its
count to one counter in every row, chosen by that row's hash of the item. An
estimate is the smallest of the item's counters: other items that share a
counter can only push it up, so an estimate is never below the true count.

Error bound (Cormode and Muthukrishnan, 2005), with N the total of all counts
added: an estimate is at most the true count + epsilon * N with probability at
least 1 - delta, where epsilon = e / width and delta = e^-depth.

Hashing uses BLAKE2b with the row number and a seed as salt, so the same item
lands in the same counters in every process and on every machine. Python's
built-in hash() of a string changes with every process, which would break
merging sketches from different Flink workers or restoring a checkpoint.
"""

from __future__ import annotations

import math
from array import array
from hashlib import blake2b

# Defaults for S4: about 1000 job ads per Country and day with about 7 items
# each give N = 7000, so the bound is 7000 * e / 2048 = 9 with probability
# 1 - e^-5 = 99.3%. With the taxonomy's 100-200 entries in 2048 columns an
# item rarely shares a counter in all 5 rows, so most estimates are exact.
WIDTH = 2048
DEPTH = 5


class CountMinSketch:
    """Approximate counts of items (strings), never below the true count.

    Two sketches can be merged only if they have the same width, depth and
    seed; the merge is the sketch of both streams together.
    """

    def __init__(self, width: int = WIDTH, depth: int = DEPTH, seed: int = 0):
        if width < 1 or depth < 1:
            raise ValueError("width and depth must be at least 1")
        if not 0 <= seed < 2**64:
            raise ValueError("seed must fit in 64 bits")
        self.width = width
        self.depth = depth
        self.seed = seed
        self.total = 0  # N in the error bound: every count ever added
        self._table = array("Q", bytes(8 * width * depth))  # row by row

    @classmethod
    def from_error(cls, epsilon: float, delta: float, seed: int = 0) -> "CountMinSketch":
        """A sketch whose estimates are within epsilon * N with probability 1 - delta."""
        if not (0 < epsilon < 1 and 0 < delta < 1):
            raise ValueError("epsilon and delta must be between 0 and 1")
        return cls(math.ceil(math.e / epsilon), math.ceil(math.log(1 / delta)), seed)

    @property
    def epsilon(self) -> float:
        return math.e / self.width

    @property
    def delta(self) -> float:
        return math.exp(-self.depth)

    def add(self, item: str, count: int = 1) -> None:
        if count < 0:
            raise ValueError("a Count-Min Sketch only counts up")
        for cell in self._cells(item):
            self._table[cell] += count
        self.total += count

    def estimate(self, item: str) -> int:
        """The item's count or a little more, never less."""
        return min(self._table[cell] for cell in self._cells(item))

    def merge(self, other: "CountMinSketch") -> None:
        """Add other's counts to this sketch, as if both streams went into it."""
        if (self.width, self.depth, self.seed) != (other.width, other.depth, other.seed):
            raise ValueError("only sketches with the same width, depth and seed can be merged")
        for cell, value in enumerate(other._table):
            self._table[cell] += value
        self.total += other.total

    def _cells(self, item: str) -> list[int]:
        """The item's counter in each row, as positions in the flat table."""
        data = item.encode("utf-8")
        return [
            row * self.width + self._column(data, row)
            for row in range(self.depth)
        ]

    def _column(self, data: bytes, row: int) -> int:
        salt = self.seed.to_bytes(8, "little") + row.to_bytes(8, "little")
        digest = blake2b(data, digest_size=8, salt=salt).digest()
        return int.from_bytes(digest, "little") % self.width

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CountMinSketch):
            return NotImplemented
        return ((self.width, self.depth, self.seed, self.total, self._table)
                == (other.width, other.depth, other.seed, other.total, other._table))

    def __repr__(self) -> str:
        return f"CountMinSketch(width={self.width}, depth={self.depth}, seed={self.seed}, total={self.total})"
