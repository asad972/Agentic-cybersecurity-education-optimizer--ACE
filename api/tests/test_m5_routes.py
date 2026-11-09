from datetime import date, datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ace_api.main import create_app


class CassandraSession:
    def __init__(self, include_demand=True):
        self.calls = []
        self.include_demand = include_demand

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
        if "demand_by_day" in query:
            if not self.include_demand:
                return []
            return [
                SimpleNamespace(item="Cybersecurity Implementer", count=12),
                SimpleNamespace(item="Security Architect", count=4),
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
            if "DISTINCT" in query:
                return [
                    SimpleNamespace(platform=platform)
                    for platform in (
                        "cyberseek",
                        "enisa_ecsf",
                        "isc2_workforce_study",
                        "new_platform",
                    )
                ]
            return [
                SimpleNamespace(
                    platform=platform,
                    feature="career_pathway",
                    supported=True,
                    notes="Reviewed source.",
                    analysed_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
                )
                for platform in parameters
            ]
        raise AssertionError(f"Unexpected query: {query}")


class Database:
    def __init__(self, include_demand=True):
        self.session = CassandraSession(include_demand=include_demand)

    def open(self):
        pass

    def ready(self):
        return True

    def close(self):
        pass


def test_education_endpoint_reads_country_and_day_role_demand():
    database = Database()
    with TestClient(create_app(database=database)) as client:
        response = client.get("/education?country=DE&day=2026-11-02")

    assert response.status_code == 200
    assert response.json()["programmes"][0]["name"] == "Security MSc"
    assert response.json()["workforce"][0]["workforce_gap"] == 100
    assert response.json()["role_comparison"] == [
        {
            "role": "Cybersecurity Implementer",
            "programme_count": 1,
            "demand_count": 12,
        },
        {"role": "Security Architect", "programme_count": 0, "demand_count": 4},
    ]
    assert response.json()["demand_available"] is True
    assert all(call[1][0] == "DE" for call in database.session.calls)
    demand_call = next(
        call for call in database.session.calls if "demand_by_day" in call[0]
    )
    assert demand_call[1] == ("DE", date(2026, 11, 2), "role")


def test_education_endpoint_without_demand_keeps_counts_unknown():
    database = Database(include_demand=False)
    with TestClient(create_app(database=database)) as client:
        response = client.get("/education?country=DE&day=2026-11-02")

    assert response.status_code == 200
    assert response.json()["demand_available"] is False
    assert response.json()["role_comparison"][0]["demand_count"] is None


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
    assert len(platforms.json()["items"]) == 4
    assert {item["platform"] for item in platforms.json()["items"]} >= {
        "new_platform"
    }
    assert len(database.session.calls) == 4


def test_education_endpoint_rejects_non_iso_country_codes():
    with TestClient(create_app(database=Database())) as client:
        response = client.get("/education?country=Germany")

    assert response.status_code == 422
