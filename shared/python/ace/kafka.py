"""Kafka for stream stages: the Flink Kafka connector JAR, downloaded once.

PyFlink's Kafka source and sink run in Flink's Java side, so every job needs the
connector JAR. It is not on PyPI; this module downloads the pinned version from
Maven Central the first time it is needed, checks its SHA-1, and keeps it in
ACE_FLINK_JARS (default ~/.cache/ace/flink-jars). A Docker image can call
connector_jar() at build time so the container never downloads anything.

Version: 5.0.0-2.2 is the newest connector release (Maven Central, 2026-05). It
is built for Flink 2.1 and 2.2; there is no release for Flink 2.3 yet. It runs
with our apache-flink 2.3.0: a bounded Kafka source and a Kafka sink with a topic
selector were tested against the local Kafka stack. Update the three constants
together when a release for Flink 2.3 appears.
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
from pathlib import Path
from typing import Callable

CONNECTOR_VERSION = "5.0.0-2.2"
CONNECTOR_JAR = f"flink-sql-connector-kafka-{CONNECTOR_VERSION}.jar"
CONNECTOR_SHA1 = "7bf2ded1144cb8aece05adcaccbc81b2ac3ef339"
CONNECTOR_URL = (
    "https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-kafka/"
    f"{CONNECTOR_VERSION}/{CONNECTOR_JAR}"
)


def jars_dir() -> Path:
    """Where downloaded Flink JARs are kept: ACE_FLINK_JARS, or ~/.cache/ace/flink-jars."""
    configured = os.environ.get("ACE_FLINK_JARS")
    return Path(configured) if configured else Path.home() / ".cache" / "ace" / "flink-jars"


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=60) as response, open(target, "wb") as f:
        while chunk := response.read(1 << 20):
            f.write(chunk)


def connector_jar(download: Callable[[str, Path], None] = _download) -> Path:
    """The Kafka connector JAR on disk, downloaded and checked on first use.

    A file that is already there is used only if its SHA-1 matches; otherwise it
    is downloaded again. A download with the wrong SHA-1 is deleted and raises,
    so a broken or tampered JAR never reaches Flink.
    """
    folder = jars_dir()
    jar = folder / CONNECTOR_JAR
    if jar.exists() and _sha1(jar) == CONNECTOR_SHA1:
        return jar
    folder.mkdir(parents=True, exist_ok=True)
    partial = jar.with_name(jar.name + ".part")
    download(CONNECTOR_URL, partial)
    actual = _sha1(partial)
    if actual != CONNECTOR_SHA1:
        partial.unlink()
        raise RuntimeError(
            f"{CONNECTOR_JAR} from {CONNECTOR_URL} has SHA-1 {actual}, expected {CONNECTOR_SHA1}"
        )
    partial.replace(jar)
    return jar
