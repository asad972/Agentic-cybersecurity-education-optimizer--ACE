"""Read-side Cassandra lifecycle; no schema creation or application writes."""
import logging
from threading import RLock
from time import monotonic

from .config import Settings

log = logging.getLogger(__name__)


class CassandraConnection:
    def __init__(self, settings: Settings, cluster_factory=None):
        self.settings = settings
        self._factory = cluster_factory
        self._cluster = None
        self._session = None
        self._next_attempt = 0.0
        self._lock = RLock()

    @property
    def session(self):
        """Driver session for bounded reads in teammate routers."""
        with self._lock:
            if self._session is None:
                raise RuntimeError("Cassandra is unavailable")
            return self._session

    def open(self):
        with self._lock:
            if self._session is not None:
                return
            from cassandra.cluster import Cluster
            from cassandra.policies import DCAwareRoundRobinPolicy

            cluster = (self._factory or Cluster)(
                list(self.settings.cassandra_hosts),
                port=self.settings.cassandra_port,
                protocol_version=4,
                load_balancing_policy=DCAwareRoundRobinPolicy(local_dc=self.settings.cassandra_local_dc),
                connect_timeout=5,
            )
            try:
                session = cluster.connect(self.settings.cassandra_keyspace)
                session.default_timeout = 5
            except Exception:
                cluster.shutdown()
                raise
            self._cluster, self._session = cluster, session

    def ready(self) -> bool:
        with self._lock:
            if monotonic() < self._next_attempt:
                return False
            try:
                self.open()
                self._session.execute("SELECT release_version FROM system.local", timeout=3)
                return True
            except Exception as error:
                log.warning("Cassandra unavailable (%s)", type(error).__name__)
                self.close()
                self._next_attempt = monotonic() + 5
                return False

    def close(self):
        with self._lock:
            cluster = self._cluster
            self._cluster = self._session = None
            if cluster is not None:
                cluster.shutdown()
