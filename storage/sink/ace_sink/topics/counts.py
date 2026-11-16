"""Counts per Country and day: Demand (S4), Supply (S5) and the Top 10 (S3).

When two screens need the same counts in different shapes, each count is
written to every table that answers one of them (see docs/ARCHITECTURE.md).
"""

from ace_sink.rows import Row, rows_for
from ace_sink.values import day

DEMAND_TABLES = ("demand_by_day", "demand_map", "demand_trend")
SUPPLY_TABLES = ("supply_by_day", "supply_map")


def _counts(message, tables):
    country, on = message["country"], day(message["day"])
    return [
        Row(table, {"country": country, "day": on, "kind": c["kind"], "item": c["item"], "count": c["count"]})
        for c in message["counts"]
        for table in tables
    ]


@rows_for("agg.demand_daily")
def demand(message, received_at):
    rows = _counts(message, DEMAND_TABLES)
    if "job_ads" in message:
        rows.append(Row("job_ads_by_day", {
            "day": day(message["day"]),
            "country": message["country"],
            "count": message["job_ads"],
        }))
    return rows


@rows_for("agg.supply_daily")
def supply(message, received_at):
    return _counts(message, SUPPLY_TABLES)


@rows_for("agg.top10_demand")
def top10(message, received_at):
    return [
        Row("top10", {
            "country": message["country"],
            "day": day(message["day"]),
            "kind": message["kind"],
            "rank": entry["rank"],
            "item": entry["item"],
            "count": entry["count"],
        })
        for entry in message["items"]
    ]
