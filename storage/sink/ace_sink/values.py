"""Converts JSON message values to the Python types the Cassandra driver writes."""

from datetime import date, datetime, timezone


def day(text: str) -> date:
    """'2026-10-01' -> date, for CQL date columns."""
    return date.fromisoformat(text)


def month(text: str) -> str:
    """'2026-10-01' -> '2026-10', the month partition of raw tables."""
    return day(text).strftime("%Y-%m")


def timestamp(text: str) -> datetime:
    """'2026-10-01T12:00:00Z' -> datetime in UTC, for CQL timestamp columns.

    A time without a zone is read as UTC, as ace.stage does, never as local time.
    """
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
