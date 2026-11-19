from datetime import date, datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ace_api.main import create_app


class CassandraSession:
    def __init__(self, include_demand=True, include_job_ads=False):
        self.calls = []
        self.include_demand = include_demand
        self.include_job_ads = include_job_ads

    def execute(self, query, parameters):
        self.calls.append((query, parameters))
        if query.startswith("SELECT ecsf_roles"):
            return [
                SimpleNamespace(ecsf_roles={"Cybersecurity Implementer"}),
                SimpleNamespace(ecsf_roles={"cybersecurity implementer", "Cybersecurity Implementer", "Penetration Tester"}),
            ]
        if "education_programmes" in query:
            return [
                SimpleNamespace(
                    programme_id="course-1",
                    name="Security MSc",
                    institution="Example University",
                    level="master",
                    ecsf_roles={"Cybersecurity Implementer", "Cybersecurity Educator"},
                    url="https://example.test/course",
                )
            ]
        if "workforce_baseline_by_geography" in query:
            if parameters[:2] == ("country", "CN"):
                return [
                    SimpleNamespace(
                        year=2023,
                        workforce_size=None,
                        workforce_gap=1_720_941,
                        workforce_growth_percent=None,
                        workforce_gap_growth_percent=None,
                        source="ISC2 Cybersecurity Workforce Study 2023",
                        source_reference="Figure 2-B, p. 12",
                    )
                ]
            if parameters[:2] != ("region", "Europe"):
                return []
            return [
                SimpleNamespace(
                    year=2024,
                    workforce_size=1_300_023,
                    workforce_gap=392_320,
                    workforce_growth_percent=-0.7,
                    workforce_gap_growth_percent=12.8,
                    source="isc2_2024",
                    source_reference="Figures 2 and 3",
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
                SimpleNamespace(item="Cybersecurity Implementer", count=10),
                SimpleNamespace(item="cybersecurity implementer", count=2),
                SimpleNamespace(item="Penetration tester", count=0),
                SimpleNamespace(item="Security Architect", count=4),
            ]
        if "job_ads_by_day" in query:
            return [SimpleNamespace(count=20)] if self.include_job_ads else []
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
    def __init__(self, include_demand=True, include_job_ads=False):
        self.session = CassandraSession(include_demand, include_job_ads)

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
        {"role": "Cybersecurity Educator", "programme_count": 1, "demand_count": None},
        {
            "role": "Cybersecurity Implementer",
            "programme_count": 1,
            "demand_count": 12,
        },
        {"role": "Penetration Tester", "programme_count": 0, "demand_count": 0},
    ]
    assert response.json()["demand_available"] is True
    assert response.json()["role_demand_available"] is True
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
    assert response.json()["role_demand_available"] is False
    assert response.json()["role_comparison"][0]["demand_count"] is None


def test_education_counts_programmes_beyond_the_list_limit():
    with TestClient(create_app(database=Database())) as client:
        response = client.get("/education?country=DE&limit=1")

    assert response.status_code == 200
    body = response.json()
    assert len(body["programmes"]) == 1
    assert body["role_comparison"] == [
        {"role": "Cybersecurity Implementer", "programme_count": 2, "demand_count": None},
        {"role": "Penetration Tester", "programme_count": 1, "demand_count": None},
    ]


def test_education_distinguishes_snapshot_without_roles_from_no_snapshot():
    database = Database(include_demand=False, include_job_ads=True)
    with TestClient(create_app(database=database)) as client:
        response = client.get("/education?country=DE&day=2026-11-02")

    assert response.status_code == 200
    assert response.json()["demand_available"] is True
    assert response.json()["role_demand_available"] is False
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


def test_workforce_endpoint_returns_regional_figures():
    database = Database()
    with TestClient(create_app(database=database)) as client:
        response = client.get(
            "/workforce?geography_level=region&geography=Europe"
        )

    assert response.status_code == 200
    assert response.json() == {
        "geography_level": "region",
        "geography": "Europe",
        "items": [
            {
                "year": 2024,
                "workforce_size": 1_300_023,
                "workforce_gap": 392_320,
                "workforce_growth_percent": -0.7,
                "workforce_gap_growth_percent": 12.8,
                "source": "isc2_2024",
                "source_reference": "Figures 2 and 3",
            }
        ],
    }
    query, parameters = database.session.calls[-1]
    assert "workforce_baseline_by_geography" in query
    assert parameters == ("region", "Europe", 100)


def test_workforce_endpoint_rejects_invalid_geography():
    with TestClient(create_app(database=Database())) as client:
        invalid_country = client.get(
            "/workforce?geography_level=country&geography=Europe"
        )
        lowercase_country = client.get(
            "/workforce?geography_level=country&geography=de"
        )
        eu_as_country = client.get(
            "/workforce?geography_level=country&geography=EU"
        )
        invalid_global = client.get(
            "/workforce?geography_level=global&geography=EU"
        )

    assert invalid_country.status_code == 422
    assert lowercase_country.status_code == 422
    assert eu_as_country.status_code == 422
    assert invalid_global.status_code == 422


def test_workforce_endpoint_does_not_fill_missing_country_metrics():
    with TestClient(create_app(database=Database())) as client:
        china = client.get("/workforce?geography_level=country&geography=CN")
        austria = client.get("/workforce?geography_level=country&geography=AT")

    assert china.status_code == 200
    item = china.json()["items"][0]
    assert item["year"] == 2023
    assert item["source"] == "ISC2 Cybersecurity Workforce Study 2023"
    assert item["workforce_size"] is None
    assert item["workforce_gap"] == 1_720_941
    assert austria.status_code == 200
    assert austria.json()["items"] == []
