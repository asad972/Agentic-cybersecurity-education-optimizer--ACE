"""Isolate Flink tests from prior stages' JVM/Beam worker lifecycles."""

import os
import signal
import subprocess
import sys

import pytest


# Capture configured paths before an earlier test's loopback worker changes
# os.environ. Do not inherit its temporary PYTHONPATH or gateway state.
_SESSION_ENV = os.environ.copy()


def _child_environment():
    env = {
        key: value for key, value in _SESSION_ENV.items()
        if not key.startswith(("PYFLINK_", "_PYFLINK_"))
        and key != "_PYTHON_WORKING_DIR"
    }
    env["ACE_S8_FLINK_TEST_CHILD"] = "1"
    return env


def _run_child(command, *, cwd, env, timeout=120):
    child = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Pytest owns JVM and Beam descendants. Killing only pytest leaves
        # them alive (and their inherited stdout/stderr pipes open).
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                           capture_output=True, check=False, timeout=10)
        else:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        stdout, stderr = child.communicate(timeout=10)
        pytest.fail(f"S8 Flink child timed out after {timeout}s; process tree terminated.\n"
                    + stdout + stderr, pytrace=False)
    if child.returncode:
        pytest.fail(stdout + stderr, pytrace=False)


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if not pyfuncitem.get_closest_marker("flink") or os.environ.get("ACE_S8_FLINK_TEST_CHILD") == "1":
        return None
    # The shared-stage + S4 + S8 sequence can hang when all reuse the same
    # loopback workers. The test still runs the real, unchanged shared runner.
    # With shared/python/tests also selected, pytest may choose that package
    # as rootdir. An absolute path keeps an outside stage's node selectable.
    node = f"{pyfuncitem.path.resolve()}::{pyfuncitem.nodeid.partition('::')[2]}"
    _run_child(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
        cwd=pyfuncitem.config.rootpath, env=_child_environment(),
        timeout=120,
    )
    return True
