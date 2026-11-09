"""Threat counts (S7), emerging skills (S9), hiring companies (S10), alerts (S6, S8),
platform analysis and the orchestrator's decisions."""

import json
from uuid import UUID

from ace_sink.rows import Row, Unstorable, rows_for
from ace_sink.values import day, timestamp


def _threats(message, table, **window):
    """One row per threat type in the message's counts."""
    return [
        Row(table, {"country": message["country"], "day": day(message["day"]), **window,
                    "threat_type": threat_type, "count": count})
        for threat_type, count in message["counts"].items()
    ]


@rows_for("agg.threats_hourly")
def threats_hourly(message, received_at):
    if "hour" not in message:
        raise Unstorable("an agg.threats_hourly message needs an hour")
    return _threats(message, "threats_by_hour", hour=message["hour"])


@rows_for("agg.threats_daily")
def threats_daily(message, received_at):
    return _threats(message, "threats_by_day")


@rows_for("agg.emerging_skills")
def emerging_skills(message, received_at):
    return [
        Row("emerging_skills", {
            "country": message["country"],
            "day": day(message["day"]),
            "mention": entry["mention"],
            "count": entry["count"],
            "max_error": entry.get("max_error"),
        })
        for entry in message["items"]
    ]


@rows_for("agg.companies_daily")
def companies(message, received_at):
    return [Row("companies_by_day", {
        "day": day(message["day"]),
        "country": message["country"],
        "companies_day": message["companies_day"],
        "companies_30d": message["companies_30d"],
        "missing_company": message.get("missing_company"),
    })]


@rows_for("alerts.drift")
@rows_for("alerts.threats")
def alert(message, received_at):
    try:
        score = float(message["score"])
    except OverflowError:
        raise Unstorable(f"score {message['score']} is too large") from None
    return [Row("alerts", {
        "country": message["country"],
        "day": day(message["day"]),
        "created_at": timestamp(message["created_at"]),
        "alert_type": message["alert_type"],
        "subject": message["subject"],
        "score": score,
        "details": message.get("details"),
    })]


@rows_for("analysis.platforms")
def platform(message, received_at):
    return [Row("platform_analysis", {
        "platform": message["platform"],
        "feature": message["feature"],
        "supported": message["supported"],
        "notes": message.get("notes"),
        "analysed_at": timestamp(message["analysed_at"]),
    })]


@rows_for("tasks.collect")
def orchestrator_decision(message, received_at):
    created_at = timestamp(message["created_at"])
    return [Row("orchestrator_decisions", {
        "day": created_at.date(),
        "created_at": created_at,
        "task_id": UUID(message["task_id"]),
        "agent": message["agent"],
        "action": message["action"],
        "params": json.dumps(message["params"], sort_keys=True),
        "reason": message["reason"],
        "decided_by": message["decided_by"],
    })]
