from fastapi import APIRouter, Request
from fastapi.testclient import TestClient
import pytest

from ace_api.config import Settings
from ace_api.main import create_app


class Database:
    def __init__(self, available=True):
        self.available = available
        self.opened = self.closed = False

    def open(self):
        self.opened = True
        if not self.available:
            raise ConnectionError("private connection detail")

    def ready(self):
        return self.available

    def close(self):
        self.closed = True


def test_service_lifecycle_and_readiness():
    database = Database()
    with TestClient(create_app(database=database)) as client:
        assert database.opened
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json() == {"status": "ready", "database": "connected"}
    assert database.closed


def test_database_outage_preserves_liveness_and_recovers():
    database = Database(available=False)
    with TestClient(create_app(database=database)) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/ready")
        assert response.status_code == 503
        assert "private" not in response.text
        database.available = True
        assert client.get("/ready").status_code == 200
    assert database.closed


def test_teammate_router_can_access_database():
    router = APIRouter()

    @router.get("/example")
    def example(request: Request):
        return {"available": request.app.state.database.ready()}

    with TestClient(create_app(database=Database(), extra_routers=[router])) as client:
        assert client.get("/example").json() == {"available": True}
        assert "/example" in client.get("/openapi.json").json()["paths"]


def test_cors_only_allows_configured_origins():
    with TestClient(create_app(database=Database())) as client:
        response = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        response = client.get("/health", headers={"Origin": "https://unrelated.example"})
        assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("kwargs", [{"cassandra_hosts": ()}, {"cassandra_port": 0},
                                    {"cassandra_keyspace": "ace; DROP TABLE x"}])
def test_invalid_database_settings(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)
