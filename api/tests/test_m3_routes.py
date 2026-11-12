from datetime import datetime, timezone
from math import nan
from types import SimpleNamespace

from cassandra import DriverException
from fastapi.testclient import TestClient
import pytest

from ace_api.main import create_app


def gap_row(skill="Cloud Security", demand=120, supply=42, gap_ratio=120 / 42):
    return SimpleNamespace(
        skill=skill,
        demand=demand,
        supply=supply,
        gap_ratio=gap_ratio,
    )


def forecast_row(
    target_month="2026-11",
    predicted=138.4,
    lower=112.7,
    upper=165.2,
    model="sarima",
    trained_at=datetime(2026, 11, 9, 2),
):
    return SimpleNamespace(
        target_month=target_month,
        predicted=predicted,
        lower=lower,
        upper=upper,
        model=model,
        trained_at=trained_at,
    )


class Session:
    def __init__(self, gap=(), forecast=(), error=None):
        self.gap = list(gap)
        self.forecast = list(forecast)
        self.error = error
        self.calls = []

    def execute(self, query, parameters, timeout):
        self.calls.append((query, parameters, timeout))
        if self.error:
            raise self.error
        if "FROM skills_gap" in query:
            return self.gap
        if "FROM forecasts" in query:
            return self.forecast
        raise AssertionError(f"Unexpected query: {query}")


class Database:
    def __init__(self, session=None, available=True):
        self.session = session or Session()
        self.available = available

    def open(self):
        pass

    def ready(self):
        return self.available

    def close(self):
        pass


def client_for(session=None, available=True):
    return TestClient(create_app(database=Database(session, available)))


def test_gap_returns_alphabetical_partition_and_preserves_null_ratio():
    session = Session(
        gap=[
            gap_row("Terraform", demand=15, supply=10, gap_ratio=None),
            gap_row("AWS Security", demand=20, supply=40, gap_ratio=0.5),
        ]
    )
    with client_for(session) as client:
        response = client.get("/gap?country=DE&month=2026-10")

    assert response.status_code == 200
    assert response.json() == {
        "country": "DE",
        "month": "2026-10",
        "items": [
            {"skill": "AWS Security", "demand": 20, "supply": 40, "gap_ratio": 0.5},
            {"skill": "Terraform", "demand": 15, "supply": 10, "gap_ratio": None},
        ],
    }
    assert session.calls == [
        (
            "SELECT skill, demand, supply, gap_ratio FROM skills_gap "
            "WHERE country = %s AND month = %s LIMIT 501",
            ("DE", "2026-10"),
            5,
        )
    ]


def test_forecast_returns_ordered_horizon_and_utc_timestamps():
    session = Session(
        forecast=[
            forecast_row("2026-12", trained_at=datetime(2026, 11, 9, 3, tzinfo=timezone.utc)),
            forecast_row("2026-11"),
        ]
    )
    with client_for(session) as client:
        response = client.get("/forecast?region=EU&skill=Cloud%20Security")

    assert response.status_code == 200
    payload = response.json()
    assert [item["target_month"] for item in payload["items"]] == ["2026-11", "2026-12"]
    assert payload["items"][0]["trained_at"] == "2026-11-09T02:00:00Z"
    assert session.calls == [
        (
            "SELECT target_month, predicted, lower, upper, model, trained_at "
            "FROM forecasts WHERE region = %s AND skill = %s LIMIT 13",
            ("EU", "Cloud Security"),
            5,
        )
    ]


def test_empty_partitions_return_successful_empty_items():
    with client_for(Session()) as client:
        assert client.get("/gap?country=DE&month=2026-10").json()["items"] == []
        assert client.get("/forecast?region=DE&skill=Linux").json()["items"] == []


@pytest.mark.parametrize(
    "path",
    [
        "/gap?country=de&month=2026-10",
        "/gap?country=DE&month=2026-13",
        "/gap?country=DE&month=October",
        "/forecast?region=eU&skill=Linux",
        "/forecast?region=EU&skill=%20%20",
        "/forecast?region=EU&skill=" + "a" * 201,
    ],
)
def test_invalid_query_parameters_are_rejected(path):
    with client_for() as client:
        assert client.get(path).status_code == 422


@pytest.mark.parametrize(
    "session,path",
    [
        (Session(gap=[gap_row(demand=-1)]), "/gap?country=DE&month=2026-10"),
        (Session(gap=[gap_row(gap_ratio=nan)]), "/gap?country=DE&month=2026-10"),
        (Session(forecast=[forecast_row(predicted=-1)]), "/forecast?region=EU&skill=Linux"),
        (Session(forecast=[forecast_row(predicted=nan)]), "/forecast?region=EU&skill=Linux"),
        (
            Session(forecast=[forecast_row(predicted=10, lower=11, upper=12)]),
            "/forecast?region=EU&skill=Linux",
        ),
    ],
)
def test_invalid_stored_values_return_sanitized_503(session, path):
    with client_for(session) as client:
        response = client.get(path)
    assert response.status_code == 503
    assert response.json() == {"detail": "M3 dashboard storage returned invalid data"}


def test_partition_limits_refuse_incomplete_responses():
    with client_for(Session(gap=[gap_row(f"Skill {index}") for index in range(501)])) as client:
        gap = client.get("/gap?country=DE&month=2026-10")
    with client_for(
        Session(forecast=[forecast_row(f"2027-{index:02d}") for index in range(1, 14)])
    ) as client:
        forecast = client.get("/forecast?region=EU&skill=Linux")

    assert gap.status_code == 503
    assert "read limit" in gap.json()["detail"]
    assert forecast.status_code == 503
    assert "12-month" in forecast.json()["detail"]


def test_database_failures_are_sanitized():
    with client_for(available=False) as client:
        unavailable = client.get("/gap?country=DE&month=2026-10")
    with client_for(Session(error=DriverException("private storage detail"))) as client:
        failed = client.get("/forecast?region=EU&skill=Linux")

    assert unavailable.status_code == 503
    assert "private" not in unavailable.text
    assert failed.status_code == 503
    assert "private" not in failed.text


def test_m3_routes_are_discovered_and_documented():
    with client_for() as client:
        paths = client.get("/openapi.json").json()["paths"]
    assert "/gap" in paths
    assert "/forecast" in paths
