"""GET /threats/hourly (#40) against a fake Cassandra session; no database needed."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as Row

from cassandra import DriverException
from fastapi.testclient import TestClient
import pytest

from ace_api.main import create_app
from ace_api.threat_data import ALERT_ROWS, HOUR_ROWS, NOTE

DAY = "2026-11-02"


class Session:
    """Answers each table with canned rows (per partition when a function) and records what was asked."""

    def __init__(self, tables=None, error=None):
        self.tables = tables or {}
        self.error = error
        self.calls = []

    def execute(self, query, parameters=(), timeout=None):
        self.calls.append((query, tuple(parameters), timeout))
        if self.error:
            raise self.error
        for table, rows in self.tables.items():
            if f"FROM {table} " in query:
                return rows(*parameters[:2]) if callable(rows) else rows
        return []


class Database:
    def __init__(self, session, is_ready=True):
        self.session, self.is_ready = session, is_ready

    def open(self):
        pass

    def close(self):
        pass

    def ready(self):
        return self.is_ready


def get(path, tables=None, **database):
    session = Session(tables)
    with TestClient(create_app(database=Database(session, **database))) as client:
        return client.get(path), session


def hour_rows(*rows):
    return [Row(hour=hour, threat_type=kind, count=count) for hour, kind, count in rows]


def alert(hour, subject="phishing", alert_type="threat_burst", score=0.91, details=None, day=date(2026, 11, 2)):
    # The driver returns timestamps without a zone; Cassandra stores them in UTC.
    return Row(created_at=datetime(day.year, day.month, day.day, hour), alert_type=alert_type,
               subject=subject, score=score, details=details)


def test_one_day_gives_each_hour_with_its_threat_types_and_the_bursts():
    response, session = get(f"/threats/hourly?country=hu&from={DAY}", {
        "threats_by_hour": hour_rows((9, "phishing", 4), (9, "malware", 2), (13, "phishing", 30), (2, "botnet", 1)),
        "alerts": [alert(13), alert(9, "SIEM", alert_type="drift"), alert(2, "botnet", score=0.6, details="HST=0.6")],
    })
    assert response.status_code == 200
    body = response.json()
    assert (body["country"], body["from"], body["to"]) == ("HU", DAY, DAY)  # `to` defaults to `from`
    assert body["hours"] == [
        {"hour": "2026-11-02T02:00:00Z", "total": 1, "counts": {"botnet": 1}},
        {"hour": "2026-11-02T09:00:00Z", "total": 6, "counts": {"malware": 2, "phishing": 4}},
        {"hour": "2026-11-02T13:00:00Z", "total": 30, "counts": {"phishing": 30}},
    ]  # hours without rows are left out, not reported as zero
    assert body["total"] == 37
    assert list(body["by_type"].items()) == [("phishing", 34), ("malware", 2), ("botnet", 1)]  # largest first
    # Only S8's bursts, oldest first, in UTC; drift alerts of the same partition are left out.
    assert body["bursts"] == [
        {"created_at": "2026-11-02T02:00:00Z", "threat_type": "botnet", "score": 0.6, "details": "HST=0.6"},
        {"created_at": "2026-11-02T13:00:00Z", "threat_type": "phishing", "score": 0.91, "details": ""},
    ]
    assert body["bursts_truncated"] is False and body["note"] == NOTE
    # One bounded partition read per table, with a timeout.
    assert [(query.split(" FROM ")[1].split()[0], parameters, timeout) for query, parameters, timeout in session.calls] == [
        ("threats_by_hour", ("HU", date(2026, 11, 2), HOUR_ROWS + 1), 5),
        ("alerts", ("HU", date(2026, 11, 2), ALERT_ROWS + 1), 5),
    ]
    assert all(query.endswith(" LIMIT %s") for query, _, _ in session.calls)


def test_a_range_reads_every_day_and_keeps_the_hours_in_order():
    days = {date(2026, 11, 1): hour_rows((23, "phishing", 5)), date(2026, 11, 3): hour_rows((0, "phishing", 7))}
    response, session = get("/threats/hourly?country=DE&from=2026-11-01&to=2026-11-03", {
        "threats_by_hour": lambda country, day: days.get(day, []),
        "alerts": lambda country, day: [alert(0, day=day)] if day == date(2026, 11, 3) else [],
    })
    body = response.json()
    assert [hour["hour"] for hour in body["hours"]] == ["2026-11-01T23:00:00Z", "2026-11-03T00:00:00Z"]
    assert body["total"] == 12 and [burst["created_at"] for burst in body["bursts"]] == ["2026-11-03T00:00:00Z"]
    read_days = [parameters[1] for query, parameters, _ in session.calls if "threats_by_hour" in query]
    assert read_days == [date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)]


def test_nothing_stored_is_an_empty_answer_rather_than_invented_values():
    response, _ = get(f"/threats/hourly?country=US&from={DAY}")
    assert response.status_code == 200
    body = response.json()
    assert (body["hours"], body["bursts"], body["by_type"], body["total"]) == ([], [], {}, 0)


def test_rows_that_cannot_be_placed_in_an_hour_are_ignored():
    response, _ = get(f"/threats/hourly?country=HU&from={DAY}", {
        "threats_by_hour": hour_rows((24, "phishing", 3), (None, "phishing", 3), (5, "phishing", None), (5, "malware", 1)),
        "alerts": [alert(5, score=None)],
    })
    body = response.json()
    assert body["hours"] == [{"hour": "2026-11-02T05:00:00Z", "total": 1, "counts": {"malware": 1}}]
    assert body["bursts"] == []


def test_more_alerts_than_the_limit_are_flagged_instead_of_silently_cut():
    many = [alert(hour % 24, subject=f"type {number}") for number, hour in enumerate(range(ALERT_ROWS + 1))]
    response, _ = get(f"/threats/hourly?country=HU&from={DAY}", {"alerts": many})
    body = response.json()
    assert body["bursts_truncated"] is True and len(body["bursts"]) == ALERT_ROWS


def test_an_hourly_partition_over_the_limit_is_refused_instead_of_truncated():
    rows = hour_rows(*[(number % 24, f"type {number}", 1) for number in range(HOUR_ROWS + 1)])
    response, _ = get(f"/threats/hourly?country=HU&from={DAY}", {"threats_by_hour": rows})
    assert response.status_code == 503 and "read limit" in response.json()["detail"]


today = datetime.now(timezone.utc).date()


@pytest.mark.parametrize("query", [
    "country=HU", f"from={DAY}", f"country=HUN&from={DAY}", f"country=H1&from={DAY}", f"country=&from={DAY}",
    "country=HU&from=2026-02-30", "country=HU&from=2019-12-31", f"country=HU&from={today + timedelta(days=1)}",
    f"country=HU&from={DAY}&to=2026-11-01", "country=HU&from=2026-10-01&to=2026-11-01",
])
def test_invalid_requests_are_rejected_before_any_read(query):
    response, session = get(f"/threats/hourly?{query}")
    assert response.status_code == 422 and session.calls == []


def test_31_days_is_the_longest_range():
    response, session = get("/threats/hourly?country=HU&from=2026-10-02&to=2026-11-01")
    assert response.status_code == 200
    assert len([call for call in session.calls if "threats_by_hour" in call[0]]) == 31


def test_unavailable_storage_is_a_503_without_sample_data():
    response, session = get(f"/threats/hourly?country=HU&from={DAY}", is_ready=False)
    assert response.status_code == 503 and session.calls == []


def test_driver_errors_are_a_503_and_do_not_leak_details():
    session = Session(error=DriverException("node 10.0.0.5 timed out"))
    with TestClient(create_app(database=Database(session))) as client:
        response = client.get(f"/threats/hourly?country=HU&from={DAY}")
    assert response.status_code == 503 and "10.0.0.5" not in response.text
