"""The Kafka connector JAR is downloaded once, checked, and reused. No network in these tests."""

import hashlib

import pytest

from ace import kafka


@pytest.fixture
def jars(tmp_path, monkeypatch):
    monkeypatch.setenv("ACE_FLINK_JARS", str(tmp_path))
    return tmp_path


def fake_download(content):
    """A download that writes content and records the URLs it was asked for."""
    calls = []

    def download(url, target):
        calls.append(url)
        target.write_bytes(content)

    download.calls = calls
    return download


@pytest.fixture
def pinned(monkeypatch):
    """Pin the expected SHA-1 to that of a small fake JAR."""
    content = b"fake connector jar"
    monkeypatch.setattr(kafka, "CONNECTOR_SHA1", hashlib.sha1(content).hexdigest())
    return content


def test_downloads_once_then_reuses_the_checked_file(jars, pinned):
    download = fake_download(pinned)
    first = kafka.connector_jar(download)
    second = kafka.connector_jar(download)
    assert first == second == jars / kafka.CONNECTOR_JAR
    assert first.read_bytes() == pinned
    assert download.calls == [kafka.CONNECTOR_URL]


def test_wrong_checksum_is_rejected_and_nothing_is_kept(jars, pinned):
    with pytest.raises(RuntimeError, match="expected"):
        kafka.connector_jar(fake_download(b"tampered"))
    assert list(jars.iterdir()) == []


def test_a_damaged_cached_jar_is_downloaded_again(jars, pinned):
    (jars / kafka.CONNECTOR_JAR).write_bytes(b"half a jar")
    download = fake_download(pinned)
    assert kafka.connector_jar(download).read_bytes() == pinned
    assert len(download.calls) == 1


def test_default_folder_is_in_the_home_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("ACE_FLINK_JARS", raising=False)
    monkeypatch.setattr(kafka.Path, "home", lambda: tmp_path)
    assert kafka.jars_dir() == tmp_path / ".cache" / "ace" / "flink-jars"


def test_url_and_file_name_match_the_pinned_version():
    assert kafka.CONNECTOR_JAR == f"flink-sql-connector-kafka-{kafka.CONNECTOR_VERSION}.jar"
    assert kafka.CONNECTOR_URL.endswith(f"/{kafka.CONNECTOR_VERSION}/{kafka.CONNECTOR_JAR}")
