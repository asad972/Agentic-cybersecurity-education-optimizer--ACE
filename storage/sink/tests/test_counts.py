from datetime import date, datetime, timezone

from ace_sink.rows import Row

RECEIVED = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
DAY = date(2026, 10, 1)


def demand_day(**changes):
    """Shaped like S4's output (streaming/s4_demand_frequency)."""
    message = {
        "country": "DE",
        "day": "2026-10-01",
        "job_ads": 12,
        "counts": [
            {"kind": "skill", "item": "SIEM", "count": 7},
            {"kind": "certification", "item": "CISSP", "count": 3},
        ],
    }
    message.update(changes)
    return message


def test_each_demand_count_goes_to_every_demand_table(stored):
    rows = stored("agg.demand_daily", demand_day(), RECEIVED)
    siem = {"country": "DE", "day": DAY, "kind": "skill", "item": "SIEM", "count": 7}
    for table in ("demand_by_day", "demand_map", "demand_trend"):
        assert Row(table, siem) in rows
    assert Row("job_ads_by_day", {"day": DAY, "country": "DE", "count": 12}) in rows
    assert len(rows) == 2 * 3 + 1


def test_job_ads_row_only_when_the_message_has_job_ads(stored):
    message = demand_day()
    del message["job_ads"]
    rows = stored("agg.demand_daily", message, RECEIVED)
    assert {row.table for row in rows} == {"demand_by_day", "demand_map", "demand_trend"}


def test_day_with_no_counts_still_stores_its_job_ads(stored):
    rows = stored("agg.demand_daily", demand_day(counts=[]), RECEIVED)
    assert rows == [Row("job_ads_by_day", {"day": DAY, "country": "DE", "count": 12})]


def test_supply_counts_go_to_both_supply_tables(stored):
    message = {"country": "FR", "day": "2026-10-01", "counts": [{"kind": "skill", "item": "Linux", "count": 40}]}
    linux = {"country": "FR", "day": DAY, "kind": "skill", "item": "Linux", "count": 40}
    assert stored("agg.supply_daily", message, RECEIVED) == [Row("supply_by_day", linux), Row("supply_map", linux)]


def test_top10_is_one_row_per_rank(stored):
    message = {
        "country": "DE",
        "day": "2026-10-01",
        "kind": "certification",
        "items": [
            {"rank": 1, "item": "CISSP", "count": 30},
            {"rank": 2, "item": "CompTIA Security+", "count": 21},
        ],
    }
    assert stored("agg.top10_demand", message, RECEIVED) == [
        Row("top10", {"country": "DE", "day": DAY, "kind": "certification", "rank": 1, "item": "CISSP", "count": 30}),
        Row("top10", {"country": "DE", "day": DAY, "kind": "certification", "rank": 2,
                      "item": "CompTIA Security+", "count": 21}),
    ]
