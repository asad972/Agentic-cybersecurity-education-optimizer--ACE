"""Europe map endpoints (#37) against a fake Cassandra session; no database needed."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as Row

from cassandra import DriverException
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from ace_api.main import create_app
from ace_api.map_data import COUNTRIES, METRICS, MapReader

DAY = "2026-11-02"


class Session:
    """Answers each table with canned rows and records what was asked."""

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
                return rows(parameters) if callable(rows) else rows
        return []


class Database:
    def __init__(self, session=None, is_ready=True):
        self.session = session or Session()
        self.is_ready = is_ready

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


def by_country(response):
    return {row["country"]: row["value"] for row in response.json()["data"]}


def test_meta_lists_the_map_countries_and_every_metric():
    response, session = get("/map/meta")
    body = response.json()
    assert response.status_code == 200
    assert len(body["countries"]) == 32
    assert {"code": "HU", "name": "Hungary"} in body["countries"]
    assert [metric["id"] for metric in body["metrics"]] == [
        "job_ads", "demand", "supply", "gap", "programmes", "companies", "threats"]
    # The dashboard takes the per-Skill metrics from here instead of keeping its own list.
    assert [metric["id"] for metric in body["metrics"] if metric["needs_item"]] == ["demand", "supply", "gap"]
    assert all(set(metric) == {"id", "label", "needs_item"} for metric in body["metrics"])
    assert body["date_max"] == datetime.now(timezone.utc).date().isoformat()
    assert session.calls == []  # reference labels only, no storage read


def test_job_ads_gives_one_value_per_country_and_null_where_nothing_is_stored():
    response, session = get(f"/map?metric=job_ads&date={DAY}", {
        "job_ads_by_day": [Row(country="HU", count=12), Row(country="DE", count=40),
                           Row(country="US", count=999)]})
    values = by_country(response)
    assert response.status_code == 200
    assert list(values) == list(COUNTRIES)  # outside countries are not shown
    assert (values["HU"], values["DE"], values["FR"]) == (12, 40, None)
    assert response.json()["unit"] == "job ads"
    query, parameters, timeout = session.calls[0]
    assert query.endswith("WHERE day = %s LIMIT %s")
    assert parameters == (date(2026, 11, 2), 10001)
    assert timeout == 5


@pytest.mark.parametrize("metric", ["demand", "supply"])
def test_skill_metrics_read_the_partition_of_the_exact_skill(metric):
    response, session = get(f"/map?metric={metric}&date={DAY}&item=%20Cloud%20security%20",
                            {f"{metric}_map": [Row(country="NL", count=7)]})
    assert response.status_code == 200
    assert response.json()["item"] == "Cloud security"
    assert by_country(response)["NL"] == 7
    assert session.calls[0][1] == ("skill", "Cloud security", date(2026, 11, 2), 10001)


@pytest.mark.parametrize("metric", ["demand", "supply", "gap"])
@pytest.mark.parametrize("item", ["", "&item=", "&item=%20%20"])
def test_skill_metrics_need_a_skill_name(metric, item):
    response, session = get(f"/map?metric={metric}&date={DAY}{item}")
    assert response.status_code == 422
    assert "skill name" in response.json()["detail"]
    assert session.calls == []


def test_gap_reads_the_month_of_the_date_and_keeps_missing_ratios_empty():
    def gap(parameters):
        country, month, skill, _ = parameters
        assert (month, skill) == ("2026-11", "SIEM")
        return {"HU": [Row(gap_ratio=2.5)], "DE": [Row(gap_ratio=None)]}.get(country, [])

    response, session = get(f"/map?metric=gap&date={DAY}&item=SIEM", {"skills_gap": gap})
    values = by_country(response)
    assert (values["HU"], values["DE"], values["FR"]) == (2.5, None, None)
    assert len(session.calls) == len(COUNTRIES)  # one bounded partition read per country


def test_programmes_are_counted_per_country_and_ignore_the_item():
    def programmes(parameters):
        return [Row(programme_id=f"p{n}") for n in range(3)] if parameters[0] == "IE" else []

    response, _ = get(f"/map?metric=programmes&date={DAY}&item=SIEM",
                      {"education_programmes": programmes})
    values = by_country(response)
    assert response.json()["item"] is None
    assert (values["IE"], values["HU"]) == (3, None)  # no rows is "no data", not zero


def test_hiring_companies_use_the_30_day_estimate():
    response, _ = get(f"/map?metric=companies&date={DAY}", {"companies_by_day": [
        Row(country="PL", companies_30d=18)]})
    assert by_country(response)["PL"] == 18
    assert response.json()["period"] == "30 days"


def test_threats_are_summed_over_threat_types():
    response, _ = get(f"/map?metric=threats&date={DAY}", {"threats_by_day": [
        Row(country="FR", threat_type="phishing", count=4),
        Row(country="FR", threat_type="malware", count=6),
        Row(country="ES", threat_type="ddos", count=1)]})
    values = by_country(response)
    assert (values["FR"], values["ES"], values["IT"]) == (10, 1, None)
    assert list(values) == list(COUNTRIES)  # nothing stored outside Europe, nothing added


def test_threats_also_cover_countries_outside_europe_for_the_world_map():
    response, _ = get(f"/map?metric=threats&date={DAY}", {"threats_by_day": [
        Row(country="US", threat_type="scanning", count=70),
        Row(country="DE", threat_type="phishing", count=3),
        Row(country="BR", threat_type="malware", count=5),
        Row(country="US", threat_type="malware", count=30)]})
    codes = [row["country"] for row in response.json()["data"]]
    values = by_country(response)
    assert response.status_code == 200
    assert codes[:len(COUNTRIES)] == list(COUNTRIES)  # the Europe map's countries first, null included
    assert codes[len(COUNTRIES):] == ["BR", "US"]     # then only countries with stored observations
    assert (values["DE"], values["BR"], values["US"], values["FR"]) == (3, 5, 100, None)


@pytest.mark.parametrize("metric,table,row", [
    ("job_ads", "job_ads_by_day", Row(country="US", count=9)),
    ("companies", "companies_by_day", Row(country="US", companies_30d=9)),
    ("demand", "demand_map", Row(country="US", count=9)),
])
def test_other_metrics_stay_within_the_europe_map_countries(metric, table, row):
    response, _ = get(f"/map?metric={metric}&date={DAY}&item=SIEM", {table: [row]})
    assert [entry["country"] for entry in response.json()["data"]] == list(COUNTRIES)


@pytest.mark.parametrize("query", [
    "metric=salaries&date=2026-11-02",
    "metric=job_ads",
    "metric=job_ads&date=yesterday",
    "metric=job_ads&date=2019-12-31",
    f"metric=job_ads&date={datetime.now(timezone.utc).date() + timedelta(days=1)}",
    f"metric=demand&date=2026-11-02&item={'x' * 201}",
])
def test_invalid_map_requests_are_rejected_before_any_read(query):
    response, session = get(f"/map?{query}")
    assert response.status_code == 422
    assert session.calls == []


@pytest.mark.parametrize("path", [f"/map?metric=job_ads&date={DAY}", f"/map/country?country=HU&date={DAY}"])
def test_unavailable_storage_is_a_503_without_sample_data(path):
    assert get(path, is_ready=False)[0].status_code == 503

    class Unopened(Database):
        @property
        def session(self):
            raise RuntimeError("Cassandra is unavailable")

        @session.setter
        def session(self, value):
            pass

    with TestClient(create_app(database=Unopened())) as client:
        assert client.get(path).status_code == 503


@pytest.mark.parametrize("path", [f"/map?metric=threats&date={DAY}", f"/map/country?country=HU&date={DAY}"])
def test_driver_errors_are_a_503_and_do_not_leak_details(path):
    session = Session(error=DriverException("10.0.0.5:9042 timed out"))
    with TestClient(create_app(database=Database(session))) as client:
        response = client.get(path)
    assert response.status_code == 503
    assert response.json() == {"detail": "Map storage is unavailable"}


def test_a_partition_over_the_row_limit_is_refused_instead_of_truncated():
    session = Session({"job_ads_by_day": [Row(country=str(n), count=1) for n in range(4)]})
    with pytest.raises(HTTPException) as error:
        MapReader(session, row_limit=3).values("job_ads", date(2026, 11, 2), None)
    assert error.value.status_code == 503
    assert "incomplete" in error.value.detail
    assert MapReader(session, row_limit=4).values("job_ads", date(2026, 11, 2), None)["3"] == 1


def test_country_panel_returns_top_lists_latest_workforce_and_alerts():
    def top10(parameters):
        country, day, kind, limit = parameters
        assert (country, day, limit) == ("HU", date(2026, 11, 2), 11)
        names = ["SIEM", "Python"] if kind == "skill" else ["CISSP"]
        return [Row(rank=rank, item=item, count=30 - rank) for rank, item in enumerate(names, 1)]

    response, session = get(f"/map/country?country=hu&date={DAY}", {
        "top10": top10,
        "workforce_baseline": [Row(year=2024, workforce_size=21000, workforce_gap=3500, source="isc2_2024")],
        "alerts": [Row(created_at=datetime(2026, 11, 2, 9, 30), alert_type="threat_burst",
                       subject="phishing", score=0.91, details=None)]})
    body = response.json()
    assert response.status_code == 200
    assert (body["country"], body["date"]) == ("HU", DAY)
    assert body["top_skills"] == [{"rank": 1, "item": "SIEM", "count": 29},
                                  {"rank": 2, "item": "Python", "count": 28}]
    assert body["top_certifications"] == [{"rank": 1, "item": "CISSP", "count": 29}]
    assert body["workforce"] == {"year": 2024, "workforce_size": 21000,
                                 "workforce_gap": 3500, "source": "isc2_2024"}
    # Cassandra returns naive UTC timestamps; the API must say so explicitly.
    assert body["alerts"] == [{"created_at": "2026-11-02T09:30:00+00:00", "alert_type": "threat_burst",
                               "subject": "phishing", "score": 0.91, "details": ""}]
    assert body["alerts_truncated"] is False
    assert all("%s" in query and "LIMIT" in query for query, _, _ in session.calls)


def test_country_panel_is_empty_rather_than_invented_when_nothing_is_stored():
    body = get(f"/map/country?country=MT&date={DAY}")[0].json()
    assert body["top_skills"] == body["top_certifications"] == body["alerts"] == []
    assert body["workforce"] is None


def test_country_panel_caps_alerts_at_50_and_says_so():
    alerts = [Row(created_at=datetime(2026, 11, 2, 12, tzinfo=timezone.utc) - timedelta(minutes=n),
                  alert_type="drift", subject=f"skill {n}", score=1.0, details="{}") for n in range(51)]
    body = get(f"/map/country?country=DE&date={DAY}", {"alerts": alerts})[0].json()
    assert len(body["alerts"]) == 50
    assert body["alerts_truncated"] is True


@pytest.mark.parametrize("query", ["country=US&date=2026-11-02", "country=&date=2026-11-02",
                                   "country=HU", "country=HU&date=2019-01-01"])
def test_country_panel_rejects_unknown_countries_and_dates(query):
    response, session = get(f"/map/country?{query}")
    assert response.status_code == 422
    assert session.calls == []


def test_every_metric_has_a_label_unit_period_and_note():
    assert all(len(values) == 4 and all(values) for values in METRICS.values())
