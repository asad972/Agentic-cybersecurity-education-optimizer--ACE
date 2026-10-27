"""Format reviewed platform evidence as ACE analysis.platforms messages."""

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any


def normalize_feature_evidence(
    evidence: Mapping[str, Any],
    analysed_at: datetime | None = None,
) -> dict[str, Any]:
    """Format one evidence-backed platform feature comparison.

    The caller must provide a reviewed support decision; this function does
    not infer product capabilities or generate unsupported claims.
    """
    platform = _text(evidence.get("platform"), "platform").lower()
    feature = _text(evidence.get("feature"), "feature").lower()
    supported = evidence.get("supported")
    if not isinstance(supported, bool):
        raise ValueError("supported must be a boolean evidence-based decision")

    notes = evidence.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise ValueError("notes must be text")

    timestamp = analysed_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("analysed_at must include a timezone")
    message: dict[str, Any] = {
        "platform": platform,
        "feature": feature,
        "supported": supported,
        "analysed_at": timestamp.astimezone(timezone.utc).isoformat(),
    }
    if notes:
        message["notes"] = notes.strip()
    return message


def normalize_platform_evidence(
    evidence_rows: Iterable[Mapping[str, Any]],
    analysed_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Format reviewed feature rows with one consistent analysis timestamp."""
    timestamp = analysed_at or datetime.now(timezone.utc)
    return [
        normalize_feature_evidence(row, analysed_at=timestamp)
        for row in evidence_rows
    ]


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()
