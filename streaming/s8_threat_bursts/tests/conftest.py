"""Isolate Flink tests from prior stages' JVM/Beam worker lifecycles."""

import os
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


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if not pyfuncitem.get_closest_marker("flink") or os.environ.get("ACE_S8_FLINK_TEST_CHILD") == "1":
        return None
    # The shared-stage + S4 + S8 sequence can hang when all reuse the same
    # loopback workers. The test still runs the real, unchanged shared runner.
    # With shared/python/tests also selected, pytest may choose that package
    # as rootdir. An absolute path keeps an outside stage's node selectable.
    node = f"{pyfuncitem.path.resolve()}::{pyfuncitem.nodeid.partition('::')[2]}"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
        cwd=pyfuncitem.config.rootpath, env=_child_environment(),
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        pytest.fail(result.stdout + result.stderr, pytrace=False)
    return True
