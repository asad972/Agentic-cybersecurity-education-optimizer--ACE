from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from ace_sink import rows as sink_rows
from ace_sink.rows import Row, Unstorable

RECEIVED = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
DAY = date(2026, 10, 1)


def test_hourly_threats_one_row_per_type(stored):
    message = {"country": "DE", "day": "2026-10-01", "hour": 14, "counts": {"malware": 9, "phishing": 2}}
    assert stored("agg.threats_hourly", message, RECEIVED) == [
        Row("threats_by_hour", {"country": "DE", "day": DAY, "hour": 14, "threat_type": "malware", "count": 9}),
        Row("threats_by_hour", {"country": "DE", "day": DAY, "hour": 14, "threat_type": "phishing", "count": 2}),
    ]


def test_hourly_threats_without_hour_cannot_be_stored():
    message = {"country": "DE", "day": "2026-10-01", "counts": {"malware": 9}}
    with pytest.raises(Unstorable, match="needs an hour"):
        sink_rows.rows("agg.threats_hourly", message, RECEIVED)


def test_daily_threats_one_row_per_type(stored):
    message = {"country": "FR", "day": "2026-10-01", "counts": {"ddos": 4}}
    assert stored("agg.threats_daily", message, RECEIVED) == [
        Row("threats_by_day", {"day": DAY, "country": "FR", "threat_type": "ddos", "count": 4}),
    ]


def test_emerging_skills_one_row_per_mention(stored):
    message = {
        "country": "DE",
        "day": "2026-10-01",
        "items": [{"mention": "ai red teaming", "count": 14, "max_error": 2}, {"mention": "sbom", "count": 9}],
    }
    rows = stored("agg.emerging_skills", message, RECEIVED)
    assert [row.values["mention"] for row in rows] == ["ai red teaming", "sbom"]
    assert rows[0].values["max_error"] == 2
    assert rows[1].values["max_error"] is None


def test_companies_per_country_and_day(stored):
    message = {"country": "NL", "day": "2026-10-01", "companies_day": 31, "companies_30d": 210, "missing_company": 3}
    assert stored("agg.companies_daily", message, RECEIVED) == [
        Row("companies_by_day", {"day": DAY, "country": "NL", "companies_day": 31, "companies_30d": 210,
                                 "missing_company": 3}),
    ]


def test_companies_without_missing_company_store_null():
    message = {"country": "NL", "day": "2026-10-01", "companies_day": 31, "companies_30d": 210}
    [row] = sink_rows.rows("agg.companies_daily", message, RECEIVED)
    assert row.values["missing_company"] is None


@pytest.mark.parametrize("topic, alert_type, subject", [
    ("alerts.drift", "drift", "SIEM"),
    ("alerts.threats", "threat_burst", "malware"),
])
def test_alerts_from_both_topics_go_to_one_table(stored, topic, alert_type, subject):
    message = {
        "country": "EU",
        "day": "2026-10-01",
        "created_at": "2026-10-01T23:59:00+02:00",
        "alert_type": alert_type,
        "subject": subject,
        "score": 3,
    }
    [row] = stored(topic, message, RECEIVED)
    assert row.table == "alerts"
    assert row.values["created_at"] == datetime(2026, 10, 1, 21, 59, tzinfo=timezone.utc)
    assert row.values["score"] == 3.0 and isinstance(row.values["score"], float)


def test_alert_time_with_lower_case_z_is_stored(stored):
    message = {"country": "DE", "day": "2026-10-01", "created_at": "2026-10-01t10:00:00z",
               "alert_type": "drift", "subject": "SIEM", "score": 3}
    [row] = stored("alerts.drift", message, RECEIVED)
    assert row.values["created_at"] == datetime(2026, 10, 1, 10, tzinfo=timezone.utc)


def test_alert_score_too_large_for_a_double_cannot_be_stored():
    message = {"country": "DE", "day": "2026-10-01", "created_at": "2026-10-01T10:00:00Z",
               "alert_type": "drift", "subject": "SIEM", "score": 10**400}
    with pytest.raises(Unstorable, match="too large"):
        sink_rows.rows("alerts.drift", message, RECEIVED)


def test_platform_feature(stored):
    message = {
        "platform": "CyberSeek",
        "feature": "Supply and demand heat map",
        "supported": True,
        "analysed_at": "2026-10-01T10:00:00Z",
    }
    [row] = stored("analysis.platforms", message, RECEIVED)
    assert row.values["supported"] is True
    assert row.values["notes"] is None


def test_orchestrator_decision_is_filed_under_its_utc_day(stored):
    message = {
        "task_id": "0b6c1f4e-2f0a-4c4e-9d43-7a1e2b3c4d5e",
        "agent": "job-agent",
        "action": "fetch_job_ads",
        "params": {"max_pages": 3, "country": "PL"},
        "reason": "Poland has fewer than 100 job ads this month.",
        "decided_by": "rules",
        "created_at": "2026-10-02T00:30:00+02:00",
    }
    [row] = stored("tasks.collect", message, RECEIVED)
    assert row.values["day"] == DAY
    assert row.values["task_id"] == UUID("0b6c1f4e-2f0a-4c4e-9d43-7a1e2b3c4d5e")
    assert row.values["params"] == '{"country": "PL", "max_pages": 3}'
