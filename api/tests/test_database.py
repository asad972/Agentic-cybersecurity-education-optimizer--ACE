from types import SimpleNamespace
import sys

import pytest

from ace_api.config import Settings
from ace_api.database import CassandraConnection


@pytest.fixture(autouse=True)
def block_native_driver_imports(monkeypatch):
    """Block the native-driver entry point, leaving pure-Python policies usable."""
    monkeypatch.setitem(sys.modules, "cassandra.cluster", None)


class Cluster:
    def __init__(self):
        self.closed = False
        self.fail_connect = False
        self.fail_read = False
        self.reads = []

    def connect(self, keyspace):
        assert keyspace == "ace"
        if self.fail_connect:
            raise ConnectionError("offline")
        return SimpleNamespace(execute=self.execute)

    def execute(self, query, timeout):
        self.reads.append(query)
        if self.fail_read:
            raise ConnectionError("lost connection")

    def shutdown(self):
        self.closed = True


def test_connection_cleanup_when_open_fails():
    cluster = Cluster()
    cluster.fail_connect = True
    database = CassandraConnection(Settings(), lambda *args, **kwargs: cluster)
    with pytest.raises(ConnectionError):
        database.open()
    assert cluster.closed
    with pytest.raises(RuntimeError):
        _ = database.session


def test_readiness_closes_broken_session_then_reconnects(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("ace_api.database.monotonic", lambda: clock[0])
    first, second = Cluster(), Cluster()
    clusters = iter([first, second])
    database = CassandraConnection(Settings(), lambda *args, **kwargs: next(clusters))
    assert database.ready()
    first.fail_read = True
    assert not database.ready()
    assert first.closed
    assert not database.ready()
    assert not second.reads
    clock[0] += 6
    assert database.ready()
    assert second.reads == ["SELECT release_version FROM system.local"]
    database.close()
    database.close()
    assert second.closed


@pytest.mark.parametrize("injected", [True, False])
def test_both_factories_receive_configured_dc(monkeypatch, injected):
    from cassandra.policies import DCAwareRoundRobinPolicy
    captured = {}
    cluster = Cluster()

    def factory(hosts, **options):
        captured.update(hosts=hosts, **options)
        return cluster

    if not injected:
        monkeypatch.setitem(sys.modules, "cassandra.cluster", SimpleNamespace(Cluster=factory))
    settings = Settings(cassandra_hosts=("db.example",), cassandra_port=9142,
                        cassandra_local_dc="team-dc")
    database = CassandraConnection(settings, factory if injected else None)
    database.open()
    assert captured["hosts"] == ["db.example"]
    assert captured["port"] == 9142 and captured["protocol_version"] == 4
    assert isinstance(captured["load_balancing_policy"], DCAwareRoundRobinPolicy)
    assert captured["load_balancing_policy"].local_dc == "team-dc"
    assert database.session.default_timeout == 5
    database.close()
    assert cluster.closed
