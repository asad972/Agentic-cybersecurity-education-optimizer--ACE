"""CLI entry point for publishing reviewed platform evidence."""

from collections.abc import Mapping
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from m5_runtime import run_agent
from platform_analysis import normalize_feature_evidence

TOPIC = "analysis.platforms"
PRODUCER = "m5-platform-analysis"


def normalize_record(
    record: Mapping[str, Any], analysed_at: datetime
) -> dict[str, Any]:
    """Normalize one evidence row, strictly decoding CSV boolean cells."""
    row = dict(record)
    supported = row.get("supported")
    if isinstance(supported, str):
        decision = supported.strip().lower()
        if decision not in {"true", "false"}:
            raise ValueError("supported in CSV must be true or false")
        row["supported"] = decision == "true"
    return normalize_feature_evidence(row, analysed_at=analysed_at)


def main() -> None:
    default_input = Path(__file__).resolve().parents[1] / "platform_evidence.json"
    run_agent(
        topic=TOPIC,
        producer_name=PRODUCER,
        normalizer_factory=lambda: partial(
            normalize_record, analysed_at=datetime.now(timezone.utc)
        ),
        input_help="Reviewed JSON, JSONL, or CSV evidence file",
        output_label="platform evidence",
        description=__doc__,
        input_default=default_input,
    )


if __name__ == "__main__":
    main()
