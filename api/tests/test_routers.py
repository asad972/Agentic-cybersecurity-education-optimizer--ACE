"""Exercise discovery using real temporary modules, without editing main.py."""
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from ace_api import routers
from ace_api.main import create_app


@pytest.fixture
def router_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(routers, "__path__", [str(tmp_path)])
    previous_modules = set(sys.modules)
    previous_attributes = set(vars(routers))
    yield tmp_path
    for name in set(sys.modules) - previous_modules:
        if name.startswith("ace_api.routers."):
            del sys.modules[name]
    for name in set(vars(routers)) - previous_attributes:
        delattr(routers, name)


def test_new_router_modules_are_served_and_documented(router_directory):
    for name in ("issue60_threats", "issue60_demand"):
        (router_directory / f"{name}.py").write_text(
            "from fastapi import APIRouter, Request\n"
            f"router = APIRouter(prefix='/{name}')\n"
            "@router.get('/status')\n"
            "def status(request: Request):\n"
            "    return {'available': request.app.state.database.ready()}\n"
        )
    database = SimpleNamespace(open=lambda: None, close=lambda: None, ready=lambda: True)
    # Repeated factory calls must not accumulate duplicate registrations.
    for _ in range(2):
        app = create_app(database=database)
        with TestClient(app) as client:
            paths = client.get("/openapi.json").json()["paths"]
            for name in ("issue60_threats", "issue60_demand"):
                path = f"/{name}/status"
                assert client.get(path).json() == {"available": True}
                assert path in paths
            discovered_paths = [path for path in paths if path.startswith("/issue60_")]
            assert discovered_paths == ["/issue60_demand/status", "/issue60_threats/status"]


def test_private_modules_helpers_and_subpackages_are_not_registered(router_directory):
    (router_directory / "_issue60_private.py").write_text("raise RuntimeError('private module')\n")
    (router_directory / "issue60_helpers.py").write_text("VALUE = 1\n")
    package = router_directory / "issue60_package"
    package.mkdir()
    (package / "__init__.py").write_text("raise RuntimeError('subpackage')\n")
    assert routers.discover_routers() == []


@pytest.mark.parametrize("source,error,match", [
    ("router = 'invalid'\n", TypeError, "router must be an APIRouter"),
    ("raise RuntimeError('broken router dependency')\n", RuntimeError, "broken router dependency"),
])
def test_broken_router_fails_app_creation(router_directory, source, error, match):
    (router_directory / "issue60_broken.py").write_text(source)
    with pytest.raises(error, match=match):
        create_app()
