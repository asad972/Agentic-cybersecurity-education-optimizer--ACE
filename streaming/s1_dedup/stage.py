"""S1: near-duplicate job posting filter per country using MinHash + LSH."""

from __future__ import annotations

import datetime
import re
from collections.abc import Iterator
from typing import Any

from datasketch import MinHash, MinHashLSH

from ace import contracts
from ace.stage import RecordStage

INPUT_TOPIC = contracts.topic("raw.jobs").name
OUTPUT_TOPIC = contracts.topic("jobs.clean").name
DUPLICATES_METRIC = "duplicates_removed_total"


def clean_and_shingle(text: str, k: int = 2) -> list[str]:
    """Tokenize normalized text and generate unigrams and k-shingles."""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    tokens = [w for w in cleaned.split() if len(w) > 1]
    if not tokens:
        return []
    shingles = list(tokens)
    if len(tokens) >= k:
        shingles.extend(
            [" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)]
        )
    return shingles


def compute_minhash(
    tokens: list[str], num_perm: int = 128, seed: int = 42
) -> MinHash:
    """Generate a deterministic MinHash signature with a fixed seed."""
    signature = MinHash(num_perm=num_perm, seed=seed)
    for token in tokens:
        signature.update(token.encode("utf-8"))
    return signature


def parse_iso_date(date_str: str | None) -> datetime.date:
    """Parse an ISO date string (YYYY-MM-DD) safely."""
    if not date_str:
        return datetime.date.min
    try:
        return datetime.date.fromisoformat(str(date_str)[:10])
    except (ValueError, TypeError):
        return datetime.date.min


class S1DedupStage(RecordStage):
    """Filter near-duplicate job postings within a country using MinHash + LSH
    over a sliding event-time window.
    """

    name = "s1-dedup"
    inputs = (INPUT_TOPIC,)

    def __init__(
        self,
        threshold: float = 0.75,
        lsh_threshold: float = 0.6,
        window_days: int = 30,
        num_perm: int = 128,
    ) -> None:
        if not (0.0 < threshold <= 1.0):
            raise ValueError("threshold must be in (0.0, 1.0]")
        if not (0.0 < lsh_threshold <= threshold):
            raise ValueError("lsh_threshold must be in (0.0, threshold]")
        if window_days <= 0:
            raise ValueError("window_days must be positive")
        if num_perm <= 0:
            raise ValueError("num_perm must be positive")

        self.threshold = threshold
        self.lsh_threshold = lsh_threshold
        self.window_days = window_days
        self.num_perm = num_perm
        self.duplicates_removed = 0
        self._flink_counter: Any = None

    def open(self, runtime_context: Any) -> None:
        """Register the Flink counter exposed to Prometheus / Grafana."""
        self._flink_counter = runtime_context.get_metrics_group().counter(
            DUPLICATES_METRIC
        )

    def _record_duplicate(self, memory: dict[str, Any]) -> None:
        """Increment checkpointed per-country and runtime duplicate counters."""
        memory["duplicates_removed"] = int(memory.get("duplicates_removed", 0)) + 1
        self.duplicates_removed += 1
        if self._flink_counter is not None:
            self._flink_counter.inc()

    def _extract_text(self, record: dict[str, Any]) -> str:
        """Extract normalized text fields defined in raw_job.schema.json."""
        title = record.get("job_title", "")
        company = record.get("company", "")
        description = record.get("description", "")
        return f"{title} {company} {description}".strip()

    def _evict_expired(
        self,
        memory: dict[str, Any],
        seen_ids: set[str],
        history: list[tuple[str, datetime.date]],
        signatures: dict[str, MinHash],
        lsh: MinHashLSH,
    ) -> None:
        """Evict entries older than window_days from seen_ids, history, signatures, and LSH."""
        latest_date: datetime.date = memory.get("latest_date", datetime.date.min)
        if latest_date == datetime.date.min:
            return

        cutoff = latest_date - datetime.timedelta(days=self.window_days)
        kept: list[tuple[str, datetime.date]] = []
        for item_id, item_date in history:
            if item_date >= cutoff or item_date == datetime.date.min:
                kept.append((item_id, item_date))
            else:
                seen_ids.discard(item_id)
                if signatures.pop(item_id, None) is not None:
                    lsh.remove(item_id)
        memory["history"] = kept

    def process(
        self, topic: str, record: dict[str, Any], memory: dict[str, Any]
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Drop exact ID repeats and near-duplicate job ads within the active window."""
        event_id: str = record["event_id"]

        seen_ids: set[str] = memory.setdefault("seen_ids", set())
        history: list[tuple[str, datetime.date]] = memory.setdefault("history", [])
        signatures: dict[str, MinHash] = memory.setdefault("signatures", {})
        lsh: MinHashLSH = memory.setdefault(
            "lsh",
            MinHashLSH(threshold=self.lsh_threshold, num_perm=self.num_perm),
        )

        # Update latest event date seen for this country, ignoring dates more than
        # 1 day ahead of the newest date seen so far so a wrong future posted_date
        # cannot empty the active 30-day window.
        post_date = parse_iso_date(record.get("posted_date"))
        if post_date != datetime.date.min:
            latest_seen: datetime.date = memory.get("latest_date", datetime.date.min)
            if latest_seen == datetime.date.min:
                memory["latest_date"] = post_date
            elif (
                post_date > latest_seen
                and (post_date - latest_seen).days <= 1
            ):
                memory["latest_date"] = post_date

        self._evict_expired(memory, seen_ids, history, signatures, lsh)
        history = memory["history"]

        # 1. Exact ID repetition: drop and count as duplicate
        if event_id in seen_ids:
            self._record_duplicate(memory)
            return

        text = self._extract_text(record)

        # 2. Ads with empty text are never treated as duplicates
        if not text:
            seen_ids.add(event_id)
            history.append((event_id, post_date))
            yield (OUTPUT_TOPIC, record)
            return

        # 3. Query LSH index (threshold=0.6) and verify exact Jaccard (>= 0.75)
        # by looking up only the candidate IDs in the signatures dict in O(1).
        tokens = clean_and_shingle(text)
        current_m = compute_minhash(tokens, num_perm=self.num_perm, seed=42)

        for cand_id in lsh.query(current_m):
            prev_m = signatures.get(cand_id)
            if prev_m is not None and current_m.jaccard(prev_m) >= self.threshold:
                self._record_duplicate(memory)
                return

        # 4. Unique posting: index in LSH/signatures if inside the active window.
        # Intentionally do not index stale out-of-order ads older than the cutoff
        # (or future-dated outliers) so bounded window memory is preserved; a later
        # copy of an already-expired ad will not be caught, which is on purpose.
        latest_seen = memory.get("latest_date", datetime.date.min)
        cutoff = (
            latest_seen - datetime.timedelta(days=self.window_days)
            if latest_seen != datetime.date.min
            else datetime.date.min
        )
        if post_date == datetime.date.min or (
            cutoff <= post_date <= latest_seen + datetime.timedelta(days=1)
        ):
            seen_ids.add(event_id)
            history.append((event_id, post_date))
            signatures[event_id] = current_m
            lsh.insert(event_id, current_m)

        yield (OUTPUT_TOPIC, record)