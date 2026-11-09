"""Give each local Flink integration test a fresh JVM/Python worker lifecycle."""
import os
import subprocess
import sys

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if not pyfuncitem.get_closest_marker("flink") or os.environ.get("ACE_S7_FLINK_TEST_CHILD") == "1":
        return None
    # Reusing PyFlink/Beam workers across several MiniCluster jobs can hang.
    # Execute the real test in a fresh interpreter, with a bounded timeout.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", pyfuncitem.nodeid],
        cwd=pyfuncitem.config.rootpath,
        env={**os.environ, "ACE_S7_FLINK_TEST_CHILD": "1"},
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode:
        pytest.fail(result.stdout + result.stderr, pytrace=False)
    return True
