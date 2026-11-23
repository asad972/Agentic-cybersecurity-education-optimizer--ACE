"""Tests for shared batch infrastructure."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
import tomllib
from types import ModuleType

import pytest

from ace.batch import (
    SKILLS_SCOPE,
    CassandraRepositoryBase,
    CassandraSettings,
    TargetMonth,
    add_months,
    month_start,
    previous_complete_month,
)


def test_skills_scope_is_fixed_eu27_eea_uk_and_switzerland() -> None:
    assert len(SKILLS_SCOPE) == len(set(SKILLS_SCOPE)) == 32
    assert {"DE", "GR", "GB", "CH", "IS", "LI", "NO"} <= set(SKILLS_SCOPE)
    assert "UK" not in SKILLS_SCOPE


def test_batch_extra_pins_cassandra_driver() -> None:
    metadata = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert metadata["project"]["optional-dependencies"]["batch"] == [
        "cassandra-driver==3.30.1"
    ]


def test_cassandra_settings_load_environment() -> None:
    settings = CassandraSettings.from_env(
        {
            "CASSANDRA_HOSTS": "db-1, db-2",
            "CASSANDRA_PORT": "9142",
            "CASSANDRA_KEYSPACE": "ace_test",
            "CASSANDRA_LOCAL_DC": "dc1",
            "CASSANDRA_USERNAME": "ace",
            "CASSANDRA_PASSWORD": "secret",
        }
    )
    assert settings.cassandra_hosts == ("db-1", "db-2")
    assert settings.cassandra_port == 9142
    assert settings.cassandra_keyspace == "ace_test"
    assert settings.cassandra_local_dc == "dc1"


@pytest.mark.parametrize(
    "values",
    [
        {"CASSANDRA_HOSTS": " , "},
        {"CASSANDRA_PORT": "0"},
        {"CASSANDRA_PORT": "nope"},
        {"CASSANDRA_KEYSPACE": "bad-name"},
        {"CASSANDRA_LOCAL_DC": " "},
        {"CASSANDRA_USERNAME": "ace"},
    ],
)
def test_invalid_cassandra_settings_are_rejected(values: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        CassandraSettings.from_env(values)


def _fake_driver(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False):
    calls: dict[str, object] = {}

    class Auth:
        def __init__(self, username: str, password: str) -> None:
            calls["auth"] = (username, password)

    class Policy:
        def __init__(self, *, local_dc: str) -> None:
            calls["dc"] = local_dc

    class Cluster:
        def __init__(self, hosts, **kwargs) -> None:
            calls["cluster"] = (hosts, kwargs)
            calls["shutdowns"] = 0

        def connect(self, keyspace: str):
            calls["keyspace"] = keyspace
            if fail:
                raise RuntimeError("unavailable")
            return object()

        def shutdown(self) -> None:
            calls["shutdowns"] = int(calls["shutdowns"]) + 1

    cassandra = ModuleType("cassandra")
    auth = ModuleType("cassandra.auth")
    cluster = ModuleType("cassandra.cluster")
    policies = ModuleType("cassandra.policies")
    auth.PlainTextAuthProvider = Auth  # type: ignore[attr-defined]
    cluster.Cluster = Cluster  # type: ignore[attr-defined]
    policies.DCAwareRoundRobinPolicy = Policy  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cassandra", cassandra)
    monkeypatch.setitem(sys.modules, "cassandra.auth", auth)
    monkeypatch.setitem(sys.modules, "cassandra.cluster", cluster)
    monkeypatch.setitem(sys.modules, "cassandra.policies", policies)
    return calls


def test_repository_connects_and_closes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_driver(monkeypatch)
    settings = CassandraSettings(
        cassandra_hosts=("db",),
        cassandra_username="ace",
        cassandra_password="secret",
    )
    with CassandraRepositoryBase.connect(settings) as repository:
        assert repository._session is not None
    repository.close()
    assert calls["keyspace"] == "ace"
    assert calls["auth"] == ("ace", "secret")
    assert calls["shutdowns"] == 1


def test_repository_shuts_down_after_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_driver(monkeypatch, fail=True)
    with pytest.raises(RuntimeError, match="unavailable"):
        CassandraRepositoryBase.connect(CassandraSettings())
    assert calls["shutdowns"] == 1


def test_calendar_helpers_and_target_month_boundaries() -> None:
    target = TargetMonth.parse("2024-02")
    assert target.first_day == date(2024, 2, 1)
    assert target.last_day == date(2024, 2, 29)
    assert len(target.days()) == 29
    assert target.key == "2024-02"
    assert month_start(date(2026, 11, 23)) == date(2026, 11, 1)
    assert add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert previous_complete_month(date(2026, 1, 15)) == TargetMonth(2025, 12)


@pytest.mark.parametrize("value", ["2026-1", "2026-00", "2026-13", "bad"])
def test_invalid_target_month_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        TargetMonth.parse(value)
