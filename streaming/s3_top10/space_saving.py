"""Space-Saving summary: the most frequent items of a stream in fixed memory.

Space-Saving (Metwally, Agrawal and El Abbadi, 2005) keeps k counters. A new
item takes a free counter; when none is free it takes over the counter with
the smallest count and starts from that count, remembering it as its error.
So every count is an upper bound of the true count, never more than the
error above it, and the error is at most N/k for a stream of N items.

Summaries are mergeable (Agarwal et al., 2013): the summary of two streams is
built from their two summaries alone, with the same N/k error bound. S3 keeps
one summary per Country and day and merges the last 30 into the 30-day Top 10.

Ties are always broken by item name, so the same stream gives the same Top 10
on every run and every machine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    """One monitored item: count is an upper bound, count - error a lower bound."""

    item: str
    count: int
    error: int

    @property
    def guaranteed(self) -> int:
        """The count the item has at least."""
        return self.count - self.error


def _rank(item: str, count: int) -> tuple[int, str]:
    # Highest count first, then item name A to Z.
    return -count, item


class SpaceSaving:
    """Space-Saving with capacity k counters. Plain dicts only, so it pickles."""

    def __init__(self, k: int):
        if k < 1:
            raise ValueError("k must be at least 1")
        self.k = k
        self.total = 0                                  # N: everything added so far
        self._counters: dict[str, list[int]] = {}       # item -> [count, error]

    def __len__(self) -> int:
        return len(self._counters)

    def __contains__(self, item: str) -> bool:
        return item in self._counters

    @property
    def floor(self) -> int:
        """The most an item that is not monitored can have occurred.

        0 while a counter is still free, otherwise the smallest count.
        """
        if len(self._counters) < self.k:
            return 0
        return min(count for count, _ in self._counters.values())

    def add(self, item: str, count: int = 1) -> None:
        """Count item count more times."""
        if count < 1:
            raise ValueError("count must be at least 1")
        self.total += count
        counters = self._counters
        if item in counters:
            counters[item][0] += count
        elif len(counters) < self.k:
            counters[item] = [count, 0]
        else:
            # Take over the smallest counter; on a tie, the item that ranks last.
            victim = max(counters, key=lambda i: _rank(i, counters[i][0]))
            smallest = counters.pop(victim)[0]
            counters[item] = [smallest + count, smallest]

    def estimate(self, item: str) -> int:
        """Upper bound of item's true count."""
        return self._counters[item][0] if item in self._counters else self.floor

    def top(self, n: int | None = None) -> list[Entry]:
        """The n items with the highest counts, highest first; all of them if n is None."""
        ranked = sorted(self._counters.items(), key=lambda kv: _rank(kv[0], kv[1][0]))
        return [Entry(item, count, error) for item, (count, error) in ranked[:n]]

    def merge(self, other: SpaceSaving) -> SpaceSaving:
        """A new summary of both streams together; self and other stay unchanged.

        For every item in either summary, the counts and errors are added. An
        item missing from one summary gets that summary's floor as both count
        and error, because it may have occurred up to floor times there. The k
        highest counts are kept, as in the mergeable-summaries merge. Each
        count stays an upper bound, and its error at most N/k for the combined
        N, because each floor is at most that summary's own N/k.
        """
        if other.k != self.k:
            raise ValueError(f"cannot merge capacity {self.k} with capacity {other.k}")
        floor_a, floor_b = self.floor, other.floor
        combined: dict[str, list[int]] = {}
        for item in self._counters.keys() | other._counters.keys():
            count_a, error_a = self._counters.get(item, (floor_a, floor_a))
            count_b, error_b = other._counters.get(item, (floor_b, floor_b))
            combined[item] = [count_a + count_b, error_a + error_b]
        kept = sorted(combined, key=lambda i: _rank(i, combined[i][0]))[: self.k]
        merged = SpaceSaving(self.k)
        merged.total = self.total + other.total
        merged._counters = {item: combined[item] for item in kept}
        return merged

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SpaceSaving):
            return NotImplemented
        return (self.k, self.total, self._counters) == (other.k, other.total, other._counters)

    def __repr__(self) -> str:
        return f"SpaceSaving(k={self.k}, total={self.total}, top={self.top(3)})"
