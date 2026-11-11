"""Protect test drivers from PyFlink's inherited Python-UDF worker flags."""

import os
import subprocess
from types import SimpleNamespace

from s8_threat_bursts.tests import conftest as harness


def test_child_drops_worker_flags_but_keeps_configured_paths(monkeypatch):
    setup = {"PATH": "/configured/bin", "JAVA_HOME": "/java", "FLINK_HOME": "/flink",
             "PYTHONPATH": "/configured/python", "ACE_SCHEMAS_DIR": "/schemas",
             "SystemRoot": "C:\\Windows"}
    monkeypatch.setattr(harness, "_SESSION_ENV", {
        **setup, "PYFLINK_GATEWAY_DISABLED": "1", "PYFLINK_GATEWAY_PORT": "1234",
        "_PYFLINK_CONN_INFO_PATH": "/old/gateway", "_PYTHON_WORKING_DIR": "/old/worker",
    })
    assert harness._child_environment() == {**setup, "ACE_S8_FLINK_TEST_CHILD": "1"}


def test_earlier_stage_cannot_poison_child_environment(monkeypatch, tmp_path):
    setup = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": "/configured/python"}
    monkeypatch.setattr(harness, "_SESSION_ENV", setup)
    monkeypatch.delenv("ACE_S8_FLINK_TEST_CHILD", raising=False)
    monkeypatch.setenv("PYFLINK_GATEWAY_DISABLED", "1")
    monkeypatch.setenv("PYTHONPATH", "/stale/worker/dependencies")
    item = SimpleNamespace(nodeid="s8_threat_bursts/tests/test_bursts.py::flink_case",
                           path=tmp_path / "outside root" / "test_bursts.py",
                           config=SimpleNamespace(rootpath=tmp_path),
                           get_closest_marker=lambda name: object() if name == "flink" else None)

    def run(command, *, env, cwd, **kwargs):
        assert command[-1] == f"{item.path}::flink_case" and cwd == tmp_path
        assert env == {**setup, "ACE_S8_FLINK_TEST_CHILD": "1"}
        assert kwargs["timeout"] == 120
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(harness.subprocess, "run", run)
    assert harness.pytest_pyfunc_call(item) is True
    assert os.environ["PYFLINK_GATEWAY_DISABLED"] == "1"
