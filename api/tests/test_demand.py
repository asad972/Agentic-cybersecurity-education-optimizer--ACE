"""Skills demand endpoints, tested with a fake Cassandra session (no driver, no database)."""
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from ace_api.main import create_app
from ace_api.routers import demand
from ace_api.routers.demand import CQL, router

Top = namedtuple("Top", "rank item count")
Count = namedtuple("Count", "country count")
ItemRow = namedtuple("ItemRow", "item count")
JobAds = namedtuple("JobAds", "country")
Replay = namedtuple("Replay", "event_date")

DAY = date(2026, 10, 1)


class Session:
    """Answers the prepared statements in demand.CQL from in-memory tables."""

    def __init__(self):
        self.top10 = {}           # (country, day, kind) -> [Top]
        self.demand_map = {}      # (kind, item, day) -> [Count]
        self.demand_by_day = {}   # (country, day, kind) -> [ItemRow]
        self.job_ads_days = set()
        self.replay_days = []
        self.prepared = []
        self.reads = []
        self.timeouts = []
        self.fail = None

    def prepare(self, cql):
        self.prepared.append(cql)
        return cql

    def execute(self, statement, values, timeout=None):
        assert statement in CQL.values(), "only prepared statements from CQL are executed"
        self.timeouts.append(timeout)
        if self.fail:
            raise self.fail
        values = tuple(values)
        self.reads.append((statement, values))
        if statement == CQL["top10"]:
            return list(self.top10.get(values, []))
        if statement == CQL["map"]:
            return list(self.demand_map.get(values, []))
        if statement == CQL["items"]:
            return list(self.demand_by_day.get(values, []))
        if statement == CQL["job_ads_day"]:
            return [JobAds("DE")] if values[0] in self.job_ads_days else []
        if statement == CQL["replay_days"]:
            return [Replay(day) for day in self.replay_days]
        raise AssertionError(statement)


class Database:
    def __init__(self, session=None):
        self._session = session
        self.ready_calls = 0

    @property
    def session(self):
        if self._session is None:
            raise RuntimeError("Cassandra is unavailable")
        return self._session

    def open(self):
        pass

    def ready(self):
        self.ready_calls += 1
        return self._session is not None

    def close(self):
        pass


@pytest.fixture
def session():
    return Session()


@pytest.fixture
def client(session):
    with TestClient(create_app(database=Database(session), extra_routers=[router])) as client:
        yield client


def test_top10_returns_ranked_items(client, session):
    session.top10[("DE", DAY, "skill")] = [Top(2, "Python", 30), Top(1, "SIEM", 42)]
    response = client.get("/top10", params={"country": "DE", "kind": "skill", "date": "2026-10-01"})
    assert response.status_code == 200
    assert response.json() == {"country": "DE", "kind": "skill", "requested_date": "2026-10-01",
                               "date": "2026-10-01", "items": [
        {"rank": 1, "item": "SIEM", "count": 42}, {"rank": 2, "item": "Python", "count": 30}]}
    assert session.reads == [(CQL["top10"], ("DE", DAY, "skill"))]


def test_top10_falls_back_to_the_newest_earlier_day(client, session):
    session.top10[("DE", date(2026, 9, 27), "skill")] = [Top(1, "SIEM", 40)]
    session.top10[("DE", date(2026, 9, 20), "skill")] = [Top(1, "Python", 10)]
    body = client.get("/top10", params={"country": "DE", "kind": "skill", "date": "2026-10-01"}).json()
    assert body["requested_date"] == "2026-10-01" and body["date"] == "2026-09-27"
    assert body["items"] == [{"rank": 1, "item": "SIEM", "count": 40}]
    assert len(session.reads) == 5


def test_top10_lookback_is_bounded(client, session):
    session.top10[("FR", DAY - timedelta(days=demand.TOP10_LOOKBACK + 1), "certification")] = [Top(1, "CISSP", 3)]
    response = client.get("/top10", params={"country": "FR", "kind": "certification", "date": "2026-10-01"})
    assert response.status_code == 200
    assert response.json()["date"] is None and response.json()["items"] == []
    assert len(session.reads) == demand.TOP10_LOOKBACK + 1


def test_demand_map_returns_one_value_per_country(client, session):
    session.demand_map[("skill", "SIEM", DAY)] = [Count("FR", 3), Count("DE", 12)]
    response = client.get("/demand/map", params={"kind": "skill", "item": "SIEM", "date": "2026-10-01"})
    assert response.status_code == 200
    assert response.json() == {"kind": "skill", "item": "SIEM", "date": "2026-10-01",
                               "values": [{"country": "DE", "count": 12}, {"country": "FR", "count": 3}]}


def test_demand_items_sums_the_skills_scope_most_demanded_first(client, session):
    session.demand_by_day[("DE", DAY, "skill")] = [ItemRow("SIEM", 5), ItemRow("Python", 2)]
    session.demand_by_day[("FR", DAY, "skill")] = [ItemRow("Python", 4), ItemRow("Cloud security", 6)]
    session.demand_by_day[("US", DAY, "skill")] = [ItemRow("Outside scope", 99)]
    response = client.get("/demand/items", params={"kind": "skill", "date": "2026-10-01"})
    assert response.status_code == 200
    assert response.json()["items"] == [
        {"item": "Cloud security", "count": 6}, {"item": "Python", "count": 6}, {"item": "SIEM", "count": 5}]
    countries = [values[0] for statement, values in session.reads]
    assert countries == list(demand.SKILLS_SCOPE) and len(countries) == 32


def test_skills_scope_is_eu27_eea_uk_and_switzerland():
    eu27 = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
            "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}
    eea_outside_eu = {"IS", "LI", "NO"}
    assert len(eu27) == 27
    assert len(demand.SKILLS_SCOPE) == 32
    assert set(demand.SKILLS_SCOPE) == eu27 | eea_outside_eu | {"GB", "CH"}


def test_demand_items_warns_when_a_partition_hits_the_limit(client, session, monkeypatch, caplog):
    monkeypatch.setattr(demand, "ITEMS_LIMIT", 2)
    session.demand_by_day[("DE", DAY, "skill")] = [ItemRow("SIEM", 5), ItemRow("Python", 2)]
    session.demand_by_day[("FR", DAY, "skill")] = [ItemRow("Python", 4)]
    with caplog.at_level("WARNING", logger=demand.log.name):
        response = client.get("/demand/items", params={"kind": "skill", "date": "2026-10-01"})
    assert response.status_code == 200
    warnings = [record.getMessage() for record in caplog.records]
    assert len(warnings) == 1 and "DE" in warnings[0] and "2-row limit" in warnings[0]


def test_every_read_has_a_timeout(client, session):
    client.get("/top10", params={"country": "DE", "kind": "skill", "date": "2026-10-01"})
    client.get("/demand/map", params={"kind": "skill", "item": "SIEM", "date": "2026-10-01"})
    client.get("/demand/items", params={"kind": "skill", "date": "2026-10-01"})
    client.get("/demand/latest-day")
    assert len(session.timeouts) > 4
    assert set(session.timeouts) == {demand.READ_TIMEOUT}


def test_statements_are_prepared_once_per_session(client, session):
    for _ in range(3):
        client.get("/top10", params={"country": "DE", "kind": "skill", "date": "2026-10-01"})
    client.get("/demand/items", params={"kind": "skill", "date": "2026-10-01"})
    assert session.prepared.count(CQL["top10"]) == 1
    assert session.prepared.count(CQL["items"]) == 1


def test_latest_day_finds_newest_day_with_job_ads(client, session):
    today = datetime.now(timezone.utc).date()
    session.job_ads_days = {today - timedelta(days=3), today - timedelta(days=10)}
    response = client.get("/demand/latest-day")
    assert response.status_code == 200
    assert response.json() == {"date": (today - timedelta(days=3)).isoformat()}


def test_latest_day_follows_the_replay_position(client, session):
    session.replay_days = [date(2019, 5, 20)]
    session.job_ads_days = {date(2019, 5, 19)}
    assert client.get("/demand/latest-day").json() == {"date": "2019-05-19"}


def test_latest_day_reads_are_bounded_and_404_without_data(client, session):
    session.replay_days = [date(2019, 5, 20)]
    response = client.get("/demand/latest-day")
    assert response.status_code == 404
    probes = [values for statement, values in session.reads if statement == CQL["job_ads_day"]]
    assert len(probes) == 2 * (demand.LATEST_DAY_LOOKBACK + 1)


@pytest.mark.parametrize("path,params", [
    ("/top10", {"country": "de", "kind": "skill", "date": "2026-10-01"}),
    ("/top10", {"country": "DEU", "kind": "skill", "date": "2026-10-01"}),
    ("/top10", {"country": "DE", "kind": "role", "date": "2026-10-01"}),
    ("/top10", {"country": "DE", "kind": "skill", "date": "2026-02-30"}),
    ("/top10", {"country": "DE", "kind": "skill", "date": "1700000000"}),
    ("/top10", {"country": "DE", "kind": "skill"}),
    ("/demand/map", {"kind": "language", "item": "SIEM", "date": "2026-10-01"}),
    ("/demand/map", {"kind": "skill", "item": "", "date": "2026-10-01"}),
    ("/demand/map", {"kind": "skill", "item": "x" * 201, "date": "2026-10-01"}),
    ("/demand/items", {"kind": "skill", "date": "01.10.2026"}),
])
def test_invalid_input_is_rejected_before_reading(client, session, path, params):
    assert client.get(path, params=params).status_code == 422
    assert session.reads == []


@pytest.mark.parametrize("path,params", [
    ("/top10", {"country": "DE", "kind": "skill", "date": "2026-10-01"}),
    ("/demand/map", {"kind": "skill", "item": "SIEM", "date": "2026-10-01"}),
    ("/demand/items", {"kind": "role", "date": "2026-10-01"}),
    ("/demand/latest-day", {}),
])
def test_database_errors_return_503_without_details(client, session, path, params):
    session.fail = TimeoutError("private host 10.0.0.7 timed out")
    response = client.get(path, params=params)
    assert response.status_code == 503
    assert response.json() == {"detail": "Cassandra is unavailable"}


def test_no_session_returns_503_after_one_reconnect_attempt():
    database = Database(session=None)
    with TestClient(create_app(database=database, extra_routers=[router])) as client:
        response = client.get("/top10", params={"country": "DE", "kind": "skill", "date": "2026-10-01"})
    assert response.status_code == 503
    assert database.ready_calls == 1


def test_endpoints_are_documented(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/top10", "/demand/map", "/demand/items", "/demand/latest-day"} <= set(paths)
