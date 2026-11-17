"""Regression checks for S7 subprocess isolation after another stage runs."""

import os
import subprocess
import sys
from types import SimpleNamespace

from s7_threat_frequency.tests import conftest as harness


def test_child_drops_gateway_state_but_preserves_user_setup(monkeypatch):
    setup = {
        "PATH": "/configured/bin", "JAVA_HOME": "/configured/java",
        "FLINK_HOME": "/configured/flink", "ACE_SCHEMAS_DIR": "/schemas",
        "PYTHONPATH": "/configured/python", "SystemRoot": "C:\\Windows",
    }
    inherited = {
        **setup, "PYFLINK_GATEWAY_DISABLED": "1", "PYFLINK_GATEWAY_PORT": "1234",
        "_PYFLINK_CONN_INFO_PATH": "/old/gateway", "_PYTHON_WORKING_DIR": "/old/worker",
    }
    monkeypatch.setattr(harness, "_SESSION_ENV", inherited)
    assert harness._child_environment() == {**setup, "ACE_S7_FLINK_TEST_CHILD": "1"}
    assert inherited["PYFLINK_GATEWAY_DISABLED"] == "1"


def test_hook_ignores_worker_environment_left_by_earlier_test(monkeypatch, tmp_path):
    initial = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": "/configured/python"}
    monkeypatch.setattr(harness, "_SESSION_ENV", initial)
    monkeypatch.delenv("ACE_S7_FLINK_TEST_CHILD", raising=False)
    monkeypatch.setenv("PYFLINK_GATEWAY_DISABLED", "1")
    monkeypatch.setenv("PYFLINK_GATEWAY_PORT", "1234")
    monkeypatch.setenv("PYTHONPATH", "/stale/worker/dependencies")
    monkeypatch.setenv("_PYTHON_WORKING_DIR", "/stale/worker")
    nodeid = "streaming/s7_threat_frequency/tests/test_frequency.py::flink_case"
    item = SimpleNamespace(
        nodeid=nodeid, config=SimpleNamespace(rootpath=tmp_path),
        get_closest_marker=lambda name: object() if name == "flink" else None,
    )

    def run(command, *, env, cwd, **kwargs):
        assert command == [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", nodeid]
        assert cwd == tmp_path
        assert env == {**initial, "ACE_S7_FLINK_TEST_CHILD": "1"}
        assert kwargs["timeout"] == 120
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(harness.subprocess, "run", run)
    assert harness.pytest_pyfunc_call(item) is True
    # Never unset the parent's guard: its in-process workers may still need it.
    assert os.environ["PYFLINK_GATEWAY_DISABLED"] == "1"
