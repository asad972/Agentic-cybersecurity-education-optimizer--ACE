"""Converts JSON message values to the Python types the Cassandra driver writes.

A value Python cannot read is Unstorable, so its message goes to errors.invalid
instead of stopping the sink.
"""

from datetime import date, datetime, timezone


def _unreadable(kind: str, text: str, error: ValueError) -> ValueError:
    # Imported here: ace_sink.rows imports the topic modules, which import this one.
    from ace_sink.rows import Unstorable

    return Unstorable(f"cannot read {kind} {text!r}: {error}")


def day(text: str) -> date:
    """'2026-10-01' -> date, for CQL date columns."""
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise _unreadable("date", text, error) from None


def month(text: str) -> str:
    """'2026-10-01' -> '2026-10', the month partition of raw tables."""
    return day(text).strftime("%Y-%m")


def timestamp(text: str) -> datetime:
    """'2026-10-01T12:00:00Z' -> datetime in UTC, for CQL timestamp columns.

    A time without a zone is read as UTC, as ace.stage does, never as local time.
    The schema check accepts a lower-case 't' and 'z' (RFC 3339 allows them),
    which Python cannot read, so the text is upper-cased first.
    """
    try:
        parsed = datetime.fromisoformat(text.upper())
    except ValueError as error:
        raise _unreadable("time", text, error) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
