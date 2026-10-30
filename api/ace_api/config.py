from dataclasses import dataclass
import os
import re


@dataclass(frozen=True)
class Settings:
    cassandra_hosts: tuple[str, ...] = ("localhost",)
    cassandra_port: int = 9042
    cassandra_keyspace: str = "ace"
    cassandra_local_dc: str = "datacenter1"
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

    def __post_init__(self):
        if not self.cassandra_hosts or not all(self.cassandra_hosts):
            raise ValueError("CASSANDRA_HOSTS must contain a hostname")
        if not 1 <= self.cassandra_port <= 65535:
            raise ValueError("CASSANDRA_PORT must be between 1 and 65535")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", self.cassandra_keyspace):
            raise ValueError("Invalid CASSANDRA_KEYSPACE")

    @classmethod
    def from_env(cls):
        def values(name, default):
            return tuple(part.strip() for part in os.getenv(name, default).split(",") if part.strip())

        return cls(
            cassandra_hosts=values("CASSANDRA_HOSTS", "localhost"),
            cassandra_port=int(os.getenv("CASSANDRA_PORT", "9042")),
            cassandra_keyspace=os.getenv("CASSANDRA_KEYSPACE", "ace"),
            cassandra_local_dc=os.getenv("CASSANDRA_LOCAL_DC", "datacenter1"),
            cors_origins=values("API_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"),
        )
