"""Tests for the M1 Role Explorer endpoints (Issue #14)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from cassandra import DriverException
from fastapi.testclient import TestClient
import pytest

from ace_api.main import create_app
from ace_api.roles_data import _load_role_catalog_from_dir, load_role_catalog
from ace_api.routers.roles import CQL


class FakeRoleSession:
    def __init__(self, *, include_relations: bool = True, fail: bool = False) -> None:
        self.include_relations = include_relations
        self.fail = fail
        self.prepared: list[str] = []
        self.queries: list[tuple[str, tuple]] = []

    def prepare(self, cql: str) -> str:
        self.prepared.append(cql)
        return cql

    def execute(self, statement: str, parameters: tuple = (), timeout=None):
        assert statement in CQL.values(), "only prepared statements from CQL are executed"
        assert timeout == 5
        self.queries.append((statement, tuple(parameters)))
        if self.fail:
            raise DriverException("Cassandra read timed out")
        if statement == CQL["role_map"]:
            return [
                SimpleNamespace(country="DE", count=42),
                SimpleNamespace(country="HU", count=14),
            ]
        if statement == CQL["demand_day"]:
            return [
                SimpleNamespace(kind="role", item="Cyber Incident Responder", count=42),
                SimpleNamespace(kind="skill", item="SIEM", count=31),
                SimpleNamespace(kind="certification", item="BTL1", count=12),
            ]
        if statement == CQL["supply_day"]:
            return [
                SimpleNamespace(kind="role", item="Cyber Incident Responder", count=19),
                SimpleNamespace(kind="skill", item="SIEM", count=15),
                SimpleNamespace(kind="certification", item="BTL1", count=6),
            ]
        if statement == CQL["relations"]:
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
        if statement == CQL["programmes"]:
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
        if statement == CQL["replay_days"]:
            return [SimpleNamespace(event_date=date(2026, 10, 15))]
        if statement == CQL["job_ads_day"]:
            return [SimpleNamespace(country="DE")] if parameters == (date(2026, 10, 15),) else []
        return []


class Database:
    def __init__(self, session=None, *, ready: bool = True) -> None:
        self._session = session
        self._ready = ready

    @property
    def session(self):
        if self._session is None:
            raise RuntimeError("Cassandra is unavailable")
        return self._session

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def ready(self) -> bool:
        return self._ready


def _client(session=None, *, ready: bool = True) -> TestClient:
    return TestClient(create_app(database=Database(session, ready=ready)))


def test_load_role_catalog_raises_when_taxonomy_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _load_role_catalog_from_dir.cache_clear()
    monkeypatch.setenv("ACE_TAXONOMY_DIR", str(tmp_path / "does-not-exist"))
    with pytest.raises(FileNotFoundError):
        load_role_catalog()

    empty_dir = tmp_path / "empty-taxonomy"
    empty_dir.mkdir()
    monkeypatch.setenv("ACE_TAXONOMY_DIR", str(empty_dir))
    with pytest.raises(FileNotFoundError):
        load_role_catalog()
    _load_role_catalog_from_dir.cache_clear()


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
    # Contribution CSVs do not assign ecsf_roles to certifications, so none are inferred.
    assert responder["certifications_count"] == 0


def test_get_role_detail_combines_taxonomy_and_cassandra_metrics() -> None:
    session = FakeRoleSession(include_relations=True)
    with _client(session) as client:
        response = client.get("/roles/cyber-incident-responder", params={"country": "DE"})
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "Cyber Incident Responder"
    assert body["country"] == "DE"
    # Default day resolves from replay_position / job_ads_by_day instead of empty today.
    assert body["day"] == "2026-10-15"
    assert body["demand"] == 42
    assert body["supply"] == 19

    siem = next(s for s in body["skills"] if s["skill"] == "SIEM")
    assert siem["demand"] == 31
    assert siem["supply"] == 15

    assert body["related_roles"][0]["role"] == "Cyber Threat Intelligence Specialist"
    assert body["related_roles"][0]["source"] == "cassandra"
    assert body["related_roles"][0]["overlap"] == 0.45

    assert len(body["programmes"]) == 1
    assert body["programmes"][0]["programme_id"] == "prog-de-1"


def test_get_role_demand_map_queries_demand_map_with_kind_role() -> None:
    session = FakeRoleSession()
    with _client(session) as client:
        response = client.get("/roles/cyber-incident-responder/map")
    assert response.status_code == 200
    body = response.json()
    assert body["metric"] == "demand"
    assert body["date"] == "2026-10-15"
    assert body["item"] == "Cyber Incident Responder"
    assert body["unit"] == "job ads"
    assert len(body["data"]) == 32

    by_country = {row["country"]: row["value"] for row in body["data"]}
    assert by_country["DE"] == 42
    assert by_country["HU"] == 14
    assert by_country["FR"] is None

    map_calls = [q for q in session.queries if q[0] == CQL["role_map"]]
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