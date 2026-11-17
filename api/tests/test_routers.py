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


@pytest.mark.parametrize("source,match", [
    ("router = 'invalid'\n", "router must be an APIRouter"),
    ("raise RuntimeError('broken router dependency')\n", "broken router dependency"),
    ("this is invalid python !\n", "SyntaxError"),
])
def test_broken_router_keeps_service_available(router_directory, source, match, caplog):
    (router_directory / "issue60_broken.py").write_text(source)
    database = SimpleNamespace(open=lambda: None, close=lambda: None, ready=lambda: True)
    with TestClient(create_app(database=database)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
    assert "issue60_broken" in caplog.text and match in caplog.text


def test_top_level_driver_import_is_isolated_on_windows(router_directory, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "cassandra.cluster", None)
    (router_directory / "issue60_bad_driver.py").write_text(
        "from cassandra.cluster import NoHostAvailable\n"
        "from fastapi import APIRouter\nrouter = APIRouter()\n")
    (router_directory / "issue60_good.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
        "@router.get('/good')\ndef good(): return {'ok': True}\n")
    # Reload exercises the module-level app = create_app(), not just the factory.
    import importlib
    import ace_api.main as main_module
    importlib.reload(main_module)
    with TestClient(main_module.create_app(database=SimpleNamespace(
            open=lambda: None, close=lambda: None, ready=lambda: True))) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
        assert client.get("/good").json() == {"ok": True}
    assert "issue60_bad_driver" in caplog.text


def test_discovered_reexported_and_explicit_router_registered_once(router_directory, monkeypatch):
    from fastapi import APIRouter, FastAPI
    router = APIRouter()
    @router.get("/unique")
    def unique():
        return {"ok": True}
    monkeypatch.setitem(sys.modules, "ace_api.routers.issue60_original", SimpleNamespace(router=router))
    (router_directory / "issue60_original.py").write_text("# supplied test module\n")
    (router_directory / "issue60_reexport.py").write_text(
        "from ace_api.routers.issue60_original import router\n")
    calls = []
    include = FastAPI.include_router
    def counted(app, candidate, *args, **kwargs):
        calls.append(candidate)
        return include(app, candidate, *args, **kwargs)
    monkeypatch.setattr(FastAPI, "include_router", counted)
    app = create_app(extra_routers=[router, router])
    assert calls == [router]
    assert "/unique" in app.openapi()["paths"]


def test_nested_router_cannot_shadow_service_probe(caplog):
    from fastapi import APIRouter
    child, parent = APIRouter(), APIRouter()
    @child.get("/ready")
    def ready():
        return {"wrong": True}
    parent.include_router(child)
    database = SimpleNamespace(open=lambda: None, close=lambda: None, ready=lambda: True)
    with TestClient(create_app(database=database, extra_routers=[parent])) as client:
        assert client.get("/ready").json() == {"status": "ready", "database": "connected"}
    assert "clashes with built-in paths" in caplog.text
