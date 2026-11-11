"""Give each local Flink integration test a fresh JVM/Python worker lifecycle."""
import os
import subprocess
import sys

import pytest


# Capture setup before any test starts a loopback worker. PyFlink leaves worker
# variables (including PYTHONPATH) in os.environ after S4's in-process job.
_SESSION_ENV = os.environ.copy()


def _child_environment():
    # A child is a new driver, never an existing gateway or Python UDF worker.
    # Keep user setup such as PATH, JAVA_HOME, FLINK_HOME and ACE_SCHEMAS_DIR.
    env = {
        key: value for key, value in _SESSION_ENV.items()
        if not key.startswith(("PYFLINK_", "_PYFLINK_"))
        and key != "_PYTHON_WORKING_DIR"
    }
    env["ACE_S7_FLINK_TEST_CHILD"] = "1"
    return env


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if not pyfuncitem.get_closest_marker("flink") or os.environ.get("ACE_S7_FLINK_TEST_CHILD") == "1":
        return None
    # Reusing PyFlink/Beam workers across several MiniCluster jobs can hang.
    # Execute the real test in a fresh interpreter, with a bounded timeout.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", pyfuncitem.nodeid],
        cwd=pyfuncitem.config.rootpath,
        env=_child_environment(),
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        pytest.fail(result.stdout + result.stderr, pytrace=False)
    return True
