from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_dockerfile_uses_root_context_python_311_and_nonroot_user():
    content = (ROOT / "batch" / "gap" / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.11-slim" in content
    assert "COPY batch/gap/" in content
    assert "USER ace" in content
    assert '["python", "-m", "ace_gap"]' in content


def test_manifest_contains_config_and_bounded_monthly_cronjob():
    content = (ROOT / "batch" / "gap" / "k8s" / "cronjob.yaml").read_text(encoding="utf-8")
    for expected in (
        "kind: ConfigMap",
        'GAP_MIN_SUPPLY: "20"',
        'GAP_SUPPLY_LOOKBACK_DAYS: "365"',
        "kind: CronJob",
        'schedule: "0 3 1 * *"',
        "timeZone: Etc/UTC",
        "concurrencyPolicy: Forbid",
        "startingDeadlineSeconds: 3600",
        "activeDeadlineSeconds: 3600",
        "backoffLimit: 2",
        "cpu: 250m",
        "memory: 256Mi",
        'cpu: "1"',
        "memory: 512Mi",
        "name: ace-cassandra-credentials",
        "key: username",
        "key: password",
        "optional: true",
    ):
        assert expected in content
