from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_dockerfile_uses_root_context_python_311_and_libgomp():
    content = (ROOT / "batch" / "forecast" / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.11-slim" in content
    assert "libgomp1" in content
    assert "COPY batch/forecast/" in content
    assert '["python", "-m", "ace_forecast"]' in content


def test_cronjob_has_bounded_weekly_execution_and_resources():
    content = (ROOT / "batch" / "forecast" / "k8s" / "cronjob.yaml").read_text(encoding="utf-8")
    for expected in (
        'schedule: "0 2 * * 1"',
        "timeZone: Etc/UTC",
        "concurrencyPolicy: Forbid",
        "startingDeadlineSeconds: 3600",
        "activeDeadlineSeconds: 3600",
        "backoffLimit: 2",
        "cpu: 250m",
        "memory: 512Mi",
        'cpu: "2"',
        "memory: 2Gi",
        "name: ace-cassandra-credentials",
        "key: username",
        "key: password",
        "optional: true",
    ):
        assert expected in content
