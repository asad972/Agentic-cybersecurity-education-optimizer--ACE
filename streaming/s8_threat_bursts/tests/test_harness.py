"""Protect test drivers from PyFlink's inherited Python-UDF worker flags."""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

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

    monkeypatch.setattr(harness, "_run_child", run)
    assert harness.pytest_pyfunc_call(item) is True
    assert os.environ["PYFLINK_GATEWAY_DISABLED"] == "1"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group integration")
def test_timeout_kills_descendant_and_reports_output(tmp_path):
    # Both descendants inherit the capture pipes: communicate cannot finish
    # promptly if only the immediate child is killed.
    script = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print('driver started',flush=True); "
        "print('worker diagnostic',file=sys.stderr,flush=True); time.sleep(60)"
    )
    with pytest.raises(pytest.fail.Exception, match="process tree terminated") as error:
        harness._run_child([sys.executable, "-c", script], cwd=tmp_path,
                           env=os.environ.copy(), timeout=1)
    assert "driver started" in str(error.value)
    assert "worker diagnostic" in str(error.value)


def test_windows_timeout_terminates_tree_and_reports_output(monkeypatch, tmp_path):
    class Child:
        pid = 123

        def communicate(self, timeout):
            if timeout == 120:
                raise subprocess.TimeoutExpired("pytest", timeout)
            return "stdout diagnostic", "stderr diagnostic"

    calls = []
    monkeypatch.setattr(harness, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(harness.subprocess, "Popen", lambda *args, **kwargs: Child())
    monkeypatch.setattr(harness.subprocess, "run", lambda args, **kwargs: calls.append(args))
    with pytest.raises(pytest.fail.Exception, match="stdout diagnosticstderr diagnostic"):
        harness._run_child(["pytest"], cwd=tmp_path, env={})
    assert calls == [["taskkill", "/PID", "123", "/T", "/F"]]
