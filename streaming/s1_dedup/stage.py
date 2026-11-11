import datetime
import re
from typing import Any, Iterator
from datasketch import MinHash
from ace.stage import RecordStage


def clean_and_shingle(text: str, k: int = 2) -> list[str]:
    """Tokenizes normalized text and generates unigrams and bigrams."""
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
    """Generates a deterministic MinHash signature with a fixed seed."""
    m = MinHash(num_perm=num_perm, seed=seed)
    for token in tokens:
        m.update(token.encode("utf-8"))
    return m


def parse_iso_date(date_str: str | None) -> datetime.date:
    """Parses ISO date string YYYY-MM-DD safely."""
    if not date_str:
        return datetime.date.min
    try:
        return datetime.date.fromisoformat(str(date_str)[:10])
    except (ValueError, TypeError):
        return datetime.date.min


class S1DedupStage(RecordStage):
    """Filters near-duplicate job postings within a country using MinHash

    signatures and a sliding window of recent postings.
    """

    name = "s1_dedup"
    inputs = ("raw.jobs",)

    def __init__(
        self,
        threshold: float = 0.75,
        window_days: int = 30,
        num_perm: int = 128,
    ):
        super().__init__()
        self.threshold = threshold
        self.window_days = window_days
        self.num_perm = num_perm

    def _extract_text(self, record: dict[str, Any]) -> str:
        """Extracts text strictly adhering to shared/schemas/raw_job.schema.json."""
        title = record.get("job_title", "")
        company = record.get("company", "")
        description = record.get("description", "")
        return f"{title} {company} {description}".strip()

    def process(
        self, topic: str, record: dict[str, Any], memory: dict[str, Any]
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Processes each job posting, discarding exact ID repeats and near-duplicates."""
        event_id = record.get("event_id") or record.get("job_id")
        if not event_id:
            yield ("jobs.clean", record)
            return

        if "seen_ids" not in memory:
            memory["seen_ids"] = set()
        if "history" not in memory:
            memory["history"] = []

        seen_ids: set[str] = memory["seen_ids"]
        history: list[tuple[str, datetime.date, MinHash]] = memory["history"]

        # 1. Exact ID repetition: drop immediately
        if event_id in seen_ids:
            return

        text = self._extract_text(record)
        post_date = parse_iso_date(record.get("posted_date"))

        # 2. Ads with empty text are never treated as duplicates
        if not text:
            seen_ids.add(event_id)
            yield ("jobs.clean", record)
            return

        # 3. Clean up records older than window_days relative to current ad's post_date
        if post_date != datetime.date.min:
            cutoff = post_date - datetime.timedelta(days=self.window_days)
            memory["history"] = [
                item
                for item in history
                if item[1] >= cutoff or item[1] == datetime.date.min
            ]
            history = memory["history"]

        # 4. Check similarity against remaining active window
        tokens = clean_and_shingle(text)
        current_m = compute_minhash(tokens, num_perm=self.num_perm, seed=42)

        for prev_id, _, prev_m in history:
            if current_m.jaccard(prev_m) >= self.threshold:
                # Near-duplicate detected
                return

        # 5. Ad is original: store signature and emit to jobs.clean
        seen_ids.add(event_id)
        history.append((event_id, post_date, current_m))
        yield ("jobs.clean", record)