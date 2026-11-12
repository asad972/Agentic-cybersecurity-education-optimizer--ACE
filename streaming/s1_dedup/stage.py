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
        window_days: int = 30,
        num_perm: int = 128,
    ) -> None:
        if not (0.0 < threshold <= 1.0):
            raise ValueError("threshold must be in (0.0, 1.0]")
        if window_days <= 0:
            raise ValueError("window_days must be positive")
        if num_perm <= 0:
            raise ValueError("num_perm must be positive")

        self.threshold = threshold
        self.window_days = window_days
        self.num_perm = num_perm

    def _extract_text(self, record: dict[str, Any]) -> str:
        """Extract normalized text fields defined in raw_job.schema.json."""
        title = record.get("job_title", "")
        company = record.get("company", "")
        description = record.get("description", "")
        return f"{title} {company} {description}".strip()

    def process(
        self, topic: str, record: dict[str, Any], memory: dict[str, Any]
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Drop exact ID repeats and near-duplicate job ads within the active window."""
        event_id = record.get("event_id") or record.get("job_id")
        if not event_id:
            yield (OUTPUT_TOPIC, record)
            return

        seen_ids: set[str] = memory.setdefault("seen_ids", set())
        history: list[tuple[str, datetime.date, MinHash]] = memory.setdefault(
            "history", []
        )
        lsh: MinHashLSH = memory.setdefault(
            "lsh",
            MinHashLSH(threshold=self.threshold, num_perm=self.num_perm),
        )

        # 1. Exact ID repetition: drop immediately
        if event_id in seen_ids:
            return

        text = self._extract_text(record)
        post_date = parse_iso_date(record.get("posted_date"))

        # 2. Ads with empty text are never treated as duplicates
        if not text:
            seen_ids.add(event_id)
            yield (OUTPUT_TOPIC, record)
            return

        # 3. Evict records older than window_days relative to latest seen event date
        if post_date != datetime.date.min:
            latest_seen = memory.get("latest_date", datetime.date.min)
            if post_date > latest_seen:
                memory["latest_date"] = post_date
                latest_seen = post_date

            cutoff = latest_seen - datetime.timedelta(days=self.window_days)
            kept: list[tuple[str, datetime.date, MinHash]] = []
            for item_id, item_date, item_mh in history:
                if item_date >= cutoff or item_date == datetime.date.min:
                    kept.append((item_id, item_date, item_mh))
                else:
                    lsh.remove(item_id)
            memory["history"] = kept
            history = kept

        # 4. Query LSH index for candidate matches and verify Jaccard similarity
        tokens = clean_and_shingle(text)
        current_m = compute_minhash(tokens, num_perm=self.num_perm, seed=42)

        candidate_ids = set(lsh.query(current_m))
        if candidate_ids:
            for prev_id, _, prev_m in history:
                if (
                    prev_id in candidate_ids
                    and current_m.jaccard(prev_m) >= self.threshold
                ):
                    return

        # 5. Unique posting: index in LSH/history and emit to jobs.clean
        seen_ids.add(event_id)
        history.append((event_id, post_date, current_m))
        lsh.insert(event_id, current_m)
        yield (OUTPUT_TOPIC, record)