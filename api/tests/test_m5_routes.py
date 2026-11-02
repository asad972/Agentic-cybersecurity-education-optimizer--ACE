from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ace_api.main import create_app


class CassandraSession:
    def __init__(self):
        self.calls = []

    def execute(self, query, parameters):
        self.calls.append((query, parameters))
        if "education_programmes" in query:
            return [
                SimpleNamespace(
                    programme_id="course-1",
                    name="Security MSc",
                    institution="Example University",
                    level="master",
                    ecsf_roles={"Cybersecurity Implementer"},
                    url="https://example.test/course",
                )
            ]
        if "workforce_baseline" in query:
            return [
                SimpleNamespace(
                    year=2024,
                    workforce_size=1000,
                    workforce_gap=100,
                    source="isc2_2024",
                )
            ]
        if "emerging_skills" in query:
            return [SimpleNamespace(mention="zero trust", count=4, max_error=1)]
        if "companies_by_day" in query:
            return [
                SimpleNamespace(
                    companies_day=2,
                    companies_30d=5,
                    missing_company=1,
                )
            ]
        if "platform_analysis" in query:
            return [
                SimpleNamespace(
                    platform=parameters[0],
                    feature="career_pathway",
                    supported=True,
                    notes="Reviewed source.",
                    analysed_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
                )
            ]
        raise AssertionError(f"Unexpected query: {query}")


class Database:
    def __init__(self):
        self.session = CassandraSession()

    def open(self):
        pass

    def ready(self):
        return True

    def close(self):
        pass


def test_education_endpoint_reads_only_requested_country_partitions():
    database = Database()
    with TestClient(create_app(database=database)) as client:
        response = client.get("/education?country=DE")

    assert response.status_code == 200
    assert response.json()["programmes"][0]["name"] == "Security MSc"
    assert response.json()["workforce"][0]["workforce_gap"] == 100
    assert all(call[1][0] == "DE" for call in database.session.calls)


def test_emerging_companies_and_platform_endpoints_return_live_rows():
    database = Database()
    with TestClient(create_app(database=database)) as client:
        emerging = client.get("/emerging?country=DE&day=2026-11-02")
        companies = client.get("/companies?country=DE&day=2026-11-02")
        platforms = client.get("/platforms")

    assert emerging.status_code == 200
    assert emerging.json()["items"] == [
        {"mention": "zero trust", "count": 4, "max_error": 1}
    ]
    assert companies.status_code == 200
    assert companies.json()["companies_30d"] == 5
    assert platforms.status_code == 200
    assert len(platforms.json()["items"]) == 3
    assert len(database.session.calls) == 5


def test_education_endpoint_rejects_non_iso_country_codes():
    with TestClient(create_app(database=Database())) as client:
        response = client.get("/education?country=Germany")

    assert response.status_code == 422
