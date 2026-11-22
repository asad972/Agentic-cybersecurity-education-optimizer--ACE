"""Tests for the M1 Role Explorer endpoints (Issue #14)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from cassandra import DriverException
from fastapi.testclient import TestClient

from ace_api.main import create_app


class FakeRoleSession:
    def __init__(self, *, include_relations: bool = True, fail: bool = False) -> None:
        self.include_relations = include_relations
        self.fail = fail
        self.queries: list[tuple[str, tuple]] = []

    def execute(self, query: str, parameters: tuple = (), timeout=None):
        self.queries.append((query, tuple(parameters)))
        if self.fail:
            raise DriverException("Cassandra read timed out")
        if "FROM demand_map" in query:
            return [
                SimpleNamespace(country="DE", count=42),
                SimpleNamespace(country="HU", count=14),
            ]
        if "FROM demand_by_day" in query:
            return [
                SimpleNamespace(kind="role", item="Cyber Incident Responder", count=42),
                SimpleNamespace(kind="skill", item="SIEM", count=31),
                SimpleNamespace(kind="certification", item="BTL1", count=12),
            ]
        if "FROM supply_by_day" in query:
            return [
                SimpleNamespace(kind="role", item="Cyber Incident Responder", count=19),
                SimpleNamespace(kind="skill", item="SIEM", count=15),
                SimpleNamespace(kind="certification", item="BTL1", count=6),
            ]
        if "FROM role_relations" in query:
            if not self.include_relations:
                return []
            return [
                SimpleNamespace(
                    related_role="Cyber Threat Intelligence Specialist",
                    overlap=0.45,
                ),
                SimpleNamespace(
                    related_role="Digital Forensics Investigator",
                    overlap=0.33,
                ),
            ]
        if "FROM education_programmes" in query:
            return [
                SimpleNamespace(
                    programme_id="prog-de-1",
                    name="MSc Incident Response & Forensics",
                    institution="TU Darmstadt",
                    level="master",
                    ecsf_roles={"Cyber Incident Responder", "Digital Forensics Investigator"},
                    url="https://example.edu/ir-msc",
                ),
                SimpleNamespace(
                    programme_id="prog-de-2",
                    name="BSc Governance & Compliance",
                    institution="Example Hochschule",
                    level="bachelor",
                    ecsf_roles={"Cybersecurity Auditor"},
                    url="https://example.edu/audit-bsc",
                ),
            ]
        return []


def _client(session=None, *, ready: bool = True) -> TestClient:
    db = SimpleNamespace(
        open=lambda: None,
        close=lambda: None,
        ready=lambda: ready,
        session=session,
    )
    return TestClient(create_app(database=db))


def test_list_roles_returns_all_twelve_ecsf_profiles_from_taxonomy() -> None:
    session = FakeRoleSession()
    with _client(session) as client:
        response = client.get("/roles", params={"country": "DE", "day": "2026-10-15"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["roles"]) == 12
    assert len(payload["countries"]) == 32
    responder = next(
        item for item in payload["roles"] if item["role"] == "Cyber Incident Responder"
    )
    assert responder["demand"] == 42
    assert responder["supply"] == 19
    assert responder["skills_count"] > 0
    assert responder["certifications_count"] > 0


def test_get_role_detail_combines_taxonomy_and_cassandra_metrics() -> None:
    session = FakeRoleSession(include_relations=True)
    with _client(session) as client:
        response = client.get(
            "/roles/cyber-incident-responder",
            params={"country": "DE", "day": "2026-10-15"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "Cyber Incident Responder"
    assert body["country"] == "DE"
    assert body["demand"] == 42
    assert body["supply"] == 19

    siem = next(s for s in body["skills"] if s["skill"] == "SIEM")
    assert siem["demand"] == 31
    assert siem["supply"] == 15

    btl1 = next(c for c in body["certifications"] if c["certification"] == "BTL1")
    assert btl1["demand"] == 12
    assert btl1["supply"] == 6

    assert body["related_roles"][0]["role"] == "Cyber Threat Intelligence Specialist"
    assert body["related_roles"][0]["source"] == "cassandra"
    assert body["related_roles"][0]["overlap"] == 0.45

    assert len(body["programmes"]) == 1
    assert body["programmes"][0]["programme_id"] == "prog-de-1"


def test_get_role_demand_map_queries_demand_map_with_kind_role() -> None:
    session = FakeRoleSession()
    with _client(session) as client:
        response = client.get(
            "/roles/cyber-incident-responder/map",
            params={"day": "2026-10-15"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["metric"] == "demand"
    assert body["item"] == "Cyber Incident Responder"
    assert body["unit"] == "job ads"
    assert len(body["data"]) == 32

    by_country = {row["country"]: row["value"] for row in body["data"]}
    assert by_country["DE"] == 42
    assert by_country["HU"] == 14
    assert by_country["FR"] is None

    map_calls = [q for q in session.queries if "FROM demand_map" in q[0]]
    assert len(map_calls) == 1
    assert map_calls[0][1] == ("role", "Cyber Incident Responder", date(2026, 10, 15))


def test_get_role_detail_falls_back_to_taxonomy_overlap_when_relations_empty() -> None:
    session = FakeRoleSession(include_relations=False)
    with _client(session) as client:
        response = client.get(
            "/roles/Penetration Tester",
            params={"country": "HU", "day": "2026-10-15"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "Penetration Tester"
    assert len(body["related_roles"]) > 0
    assert all(rel["source"] == "taxonomy" for rel in body["related_roles"])
    assert all(0 < rel["overlap"] <= 1 for rel in body["related_roles"])


def test_roles_endpoints_return_503_when_cassandra_is_unavailable() -> None:
    with _client(None, ready=False) as client:
        assert client.get("/roles").status_code == 503
        assert client.get("/roles/cyber-incident-responder").status_code == 503
        assert client.get("/roles/cyber-incident-responder/map").status_code == 503

    failing_session = FakeRoleSession(fail=True)
    with _client(failing_session, ready=True) as client:
        assert client.get("/roles", params={"country": "DE", "day": "2026-10-15"}).status_code == 503
        assert client.get("/roles/cyber-incident-responder", params={"day": "2026-10-15"}).status_code == 503
        assert client.get("/roles/cyber-incident-responder/map", params={"day": "2026-10-15"}).status_code == 503


def test_unknown_role_and_invalid_filters_return_expected_errors() -> None:
    session = FakeRoleSession()
    with _client(session, ready=True) as client:
        not_found = client.get("/roles/nonexistent-role")
        assert not_found.status_code == 404

        not_found_map = client.get("/roles/nonexistent-role/map")
        assert not_found_map.status_code == 404

        bad_country = client.get("/roles/penetration-tester", params={"country": "US"})
        assert bad_country.status_code == 422

        bad_date = client.get("/roles/penetration-tester", params={"day": "2019-01-01"})
        assert bad_date.status_code == 422