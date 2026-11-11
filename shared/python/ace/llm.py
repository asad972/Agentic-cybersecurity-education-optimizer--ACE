"""Validated, cached and budgeted access to ACE language models.

This is the only supported path to an LLM in ACE.  The public entry point is
``LLMClient.generate``; providers and stores are injectable so unit tests never
need a model server, Cassandra, or network access.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from types import TracebackType
from typing import Any, Generic, Protocol, Self, TypeVar, cast

from pydantic import BaseModel, ValidationError

LOG = logging.getLogger(__name__)
MICRO_EUR = Decimal("0.000001")
PER_MILLION = Decimal(1000000)
TASK_RE = re.compile(r"^[a-z][a-z0-9_]{0,99}$")

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Base class for all public LLM module failures."""


class LLMConfigurationError(LLMError):
    """The environment or caller configuration is invalid."""


class LLMProviderError(LLMError):
    """The selected provider could not produce a usable response."""


class LLMResponseError(LLMError):
    """The provider response was not valid JSON for the requested model."""


class BudgetExceededError(LLMError):
    """A hosted call would exceed the shared UTC daily budget."""


class CacheBusyError(LLMError):
    """Another worker retained the request lease for too long."""


class RecordingNotFoundError(LLMError):
    """An offline test recording did not contain the requested call."""


class ProviderAttemptError(Exception):
    """Internal normalized provider error used by the retry loop."""

    def __init__(
        self, message: str, *, retryable: bool, ambiguous_charge: bool = False
    ):
        super().__init__(message)
        self.retryable = retryable
        self.ambiguous_charge = ambiguous_charge


def _decimal(name: str, raw: str | None, *, required: bool = False) -> Decimal | None:
    if raw is None or not raw.strip():
        if required:
            raise LLMConfigurationError(f"{name} is required")
        return None
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as error:
        raise LLMConfigurationError(f"{name} must be a decimal number") from error
    if not value.is_finite() or value < 0:
        raise LLMConfigurationError(f"{name} must be finite and non-negative")
    return value


def _integer(name: str, raw: str, *, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except ValueError as error:
        raise LLMConfigurationError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise LLMConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _positive_float(name: str, raw: str, *, maximum: float) -> float:
    try:
        value = float(raw)
    except ValueError as error:
        raise LLMConfigurationError(f"{name} must be a number") from error
    if not 0 < value <= maximum:
        raise LLMConfigurationError(
            f"{name} must be greater than zero and at most {maximum}"
        )
    return value


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "local"
    local_url: str = "http://localhost:11434"
    local_model: str = "qwen3:4b"
    hosted_model: str = "claude-haiku-5-5"
    api_key: str | None = None
    daily_budget_eur: Decimal = Decimal(2)
    hosted_input_eur_per_mtok: Decimal | None = None
    hosted_output_eur_per_mtok: Decimal | None = None
    timeout_seconds: float = 60.0
    max_attempts: int = 3
    max_tokens: int = 2048
    cache_lease_seconds: int = 300
    cache_poll_seconds: float = 0.1

    def __post_init__(self) -> None:
        if self.provider not in {"local", "hosted"}:
            raise LLMConfigurationError("LLM_PROVIDER must be 'local' or 'hosted'")
        if not self.local_url.startswith(("http://", "https://")):
            raise LLMConfigurationError("LLM_LOCAL_URL must be an HTTP(S) URL")
        if not self.local_model.strip() or not self.hosted_model.strip():
            raise LLMConfigurationError("LLM model names must not be empty")
        if not self.daily_budget_eur.is_finite() or self.daily_budget_eur < 0:
            raise LLMConfigurationError("LLM_DAILY_BUDGET_EUR must be non-negative")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise LLMConfigurationError(
                "LLM_TIMEOUT_SECONDS must be finite and positive"
            )
        if not 1 <= self.max_attempts <= 10:
            raise LLMConfigurationError("LLM_MAX_ATTEMPTS must be between 1 and 10")
        if not 1 <= self.max_tokens <= 131072:
            raise LLMConfigurationError("LLM_MAX_TOKENS must be between 1 and 131072")
        if not 1 <= self.cache_lease_seconds <= 3600:
            raise LLMConfigurationError(
                "LLM_CACHE_LEASE_SECONDS must be between 1 and 3600"
            )
        if not math.isfinite(self.cache_poll_seconds) or self.cache_poll_seconds <= 0:
            raise LLMConfigurationError(
                "LLM_CACHE_POLL_SECONDS must be finite and positive"
            )
        if self.provider == "hosted":
            if not self.api_key or not self.api_key.strip():
                raise LLMConfigurationError("LLM_API_KEY is required for hosted mode")
            if (
                self.hosted_input_eur_per_mtok is None
                or self.hosted_output_eur_per_mtok is None
            ):
                raise LLMConfigurationError(
                    "hosted mode requires LLM_HOSTED_INPUT_EUR_PER_MTOK and "
                    "LLM_HOSTED_OUTPUT_EUR_PER_MTOK"
                )
            if (
                not self.hosted_input_eur_per_mtok.is_finite()
                or self.hosted_input_eur_per_mtok <= 0
                or not self.hosted_output_eur_per_mtok.is_finite()
                or self.hosted_output_eur_per_mtok <= 0
            ):
                raise LLMConfigurationError(
                    "hosted token rates must be finite and positive"
                )
        provider_calls_per_attempt = 2 if self.provider == "hosted" else 1
        if self.cache_lease_seconds < self.timeout_seconds * provider_calls_per_attempt:
            raise LLMConfigurationError(
                "LLM_CACHE_LEASE_SECONDS must cover one complete provider attempt"
            )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> LLMSettings:
        env = os.environ if environ is None else environ
        provider = env.get("LLM_PROVIDER", "local").strip().lower()
        if provider not in {"local", "hosted"}:
            raise LLMConfigurationError("LLM_PROVIDER must be 'local' or 'hosted'")
        api_key = (env.get("LLM_API_KEY") or "").strip() or None
        if provider == "hosted" and api_key is None:
            raise LLMConfigurationError("LLM_API_KEY is required for hosted mode")
        budget = cast(
            Decimal,
            _decimal(
                "LLM_DAILY_BUDGET_EUR",
                env.get("LLM_DAILY_BUDGET_EUR", "2"),
                required=True,
            ),
        )
        input_rate = _decimal(
            "LLM_HOSTED_INPUT_EUR_PER_MTOK",
            env.get("LLM_HOSTED_INPUT_EUR_PER_MTOK"),
            required=provider == "hosted",
        )
        output_rate = _decimal(
            "LLM_HOSTED_OUTPUT_EUR_PER_MTOK",
            env.get("LLM_HOSTED_OUTPUT_EUR_PER_MTOK"),
            required=provider == "hosted",
        )
        return cls(
            provider=provider,
            local_url=env.get("LLM_LOCAL_URL", "http://localhost:11434")
            .strip()
            .rstrip("/"),
            local_model=env.get("LLM_LOCAL_MODEL", "qwen3:4b").strip(),
            hosted_model=env.get("LLM_HOSTED_MODEL", "claude-haiku-5-5").strip(),
            api_key=api_key,
            daily_budget_eur=budget,
            hosted_input_eur_per_mtok=input_rate,
            hosted_output_eur_per_mtok=output_rate,
            timeout_seconds=_positive_float(
                "LLM_TIMEOUT_SECONDS", env.get("LLM_TIMEOUT_SECONDS", "60"), maximum=600
            ),
            max_attempts=_integer(
                "LLM_MAX_ATTEMPTS",
                env.get("LLM_MAX_ATTEMPTS", "3"),
                minimum=1,
                maximum=10,
            ),
            max_tokens=_integer(
                "LLM_MAX_TOKENS",
                env.get("LLM_MAX_TOKENS", "2048"),
                minimum=1,
                maximum=131072,
            ),
            cache_lease_seconds=_integer(
                "LLM_CACHE_LEASE_SECONDS",
                env.get("LLM_CACHE_LEASE_SECONDS", "300"),
                minimum=1,
                maximum=3600,
            ),
            cache_poll_seconds=_positive_float(
                "LLM_CACHE_POLL_SECONDS",
                env.get("LLM_CACHE_POLL_SECONDS", "0.1"),
                maximum=10,
            ),
        )


@dataclass(frozen=True)
class LLMRequest(Generic[T]):
    task: str
    prompt_version: str
    system_prompt: str
    prompt: str
    response_model: type[T]
    max_tokens: int
    request_hash: str


@dataclass(frozen=True)
class ProviderResponse:
    answer: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LLMResult(Generic[T]):
    value: T
    provider: str
    model: str
    cached: bool
    input_tokens: int | None
    output_tokens: int | None
    cost_eur: Decimal


@dataclass(frozen=True)
class CacheRecord:
    answer: str
    provider: str
    model: str
    cost_eur: Decimal
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime


@dataclass(frozen=True)
class ClaimAttempt:
    acquired: bool
    wait_until: datetime
    abandoned_budget_day: date | None = None
    abandoned_reservation_eur: Decimal = Decimal(0)


@dataclass(frozen=True)
class BudgetSnapshot:
    budget_eur: Decimal
    spent_eur: Decimal
    reserved_eur: Decimal


class Provider(Protocol):
    name: str
    model: str

    def count_input_tokens(self, request: LLMRequest[Any]) -> int: ...

    def generate(self, request: LLMRequest[Any]) -> ProviderResponse: ...

    def close(self) -> None: ...


class LLMStore(Protocol):
    def get_cache(self, task: str, input_hash: str) -> CacheRecord | None: ...

    def put_cache(
        self, task: str, input_hash: str, record: CacheRecord
    ) -> CacheRecord: ...

    def try_claim(
        self,
        task: str,
        input_hash: str,
        token: uuid.UUID,
        now: datetime,
        claim_until: datetime,
    ) -> ClaimAttempt: ...

    def renew_claim(
        self, task: str, input_hash: str, token: uuid.UUID, claim_until: datetime
    ) -> bool: ...

    def set_claim_reservation(
        self,
        task: str,
        input_hash: str,
        token: uuid.UUID,
        budget_day: date | None,
        amount: Decimal,
    ) -> bool: ...

    def release_claim(self, task: str, input_hash: str, token: uuid.UUID) -> None: ...

    def reserve_budget(self, day: date, limit: Decimal, amount: Decimal) -> bool: ...

    def settle_budget(self, day: date, reserved: Decimal, actual: Decimal) -> None: ...

    def release_budget(self, day: date, reserved: Decimal) -> None: ...

    def charge_abandoned(self, day: date, reserved: Decimal) -> None: ...

    def budget_snapshot(self, day: date) -> BudgetSnapshot | None: ...

    def close(self) -> None: ...


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_hash(
    *,
    task: str,
    prompt_version: str,
    provider: str,
    model: str,
    system_prompt: str,
    prompt: str,
    response_model: type[BaseModel],
    max_tokens: int,
) -> str:
    payload = {
        "task": task,
        "prompt_version": prompt_version,
        "provider": provider,
        "model": model,
        "system_prompt": system_prompt,
        "prompt": prompt,
        "response_schema": response_model.model_json_schema(),
        "max_tokens": max_tokens,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class InMemoryLLMStore:
    """Thread-safe store used by unit tests and small offline demonstrations."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: dict[tuple[str, str], CacheRecord] = {}
        self._claims: dict[tuple[str, str], dict[str, Any]] = {}
        self._budgets: dict[date, BudgetSnapshot] = {}

    def get_cache(self, task: str, input_hash: str) -> CacheRecord | None:
        with self._lock:
            return self._cache.get((task, input_hash))

    def put_cache(self, task: str, input_hash: str, record: CacheRecord) -> CacheRecord:
        with self._lock:
            return self._cache.setdefault((task, input_hash), record)

    def try_claim(
        self,
        task: str,
        input_hash: str,
        token: uuid.UUID,
        now: datetime,
        claim_until: datetime,
    ) -> ClaimAttempt:
        key = (task, input_hash)
        with self._lock:
            current = self._claims.get(key)
            if current is None:
                self._claims[key] = {
                    "token": token,
                    "until": claim_until,
                    "budget_day": None,
                    "reserved": Decimal(0),
                }
                return ClaimAttempt(True, claim_until)
            if current["until"] <= now:
                abandoned_day = current["budget_day"]
                abandoned = current["reserved"]
                self._claims[key] = {
                    "token": token,
                    "until": claim_until,
                    "budget_day": None,
                    "reserved": Decimal(0),
                }
                return ClaimAttempt(True, claim_until, abandoned_day, abandoned)
            return ClaimAttempt(False, current["until"])

    def renew_claim(
        self, task: str, input_hash: str, token: uuid.UUID, claim_until: datetime
    ) -> bool:
        with self._lock:
            current = self._claims.get((task, input_hash))
            if current is None or current["token"] != token:
                return False
            current["until"] = claim_until
            return True

    def set_claim_reservation(
        self,
        task: str,
        input_hash: str,
        token: uuid.UUID,
        budget_day: date | None,
        amount: Decimal,
    ) -> bool:
        with self._lock:
            current = self._claims.get((task, input_hash))
            if current is None or current["token"] != token:
                return False
            current["budget_day"] = budget_day
            current["reserved"] = amount
            return True

    def release_claim(self, task: str, input_hash: str, token: uuid.UUID) -> None:
        with self._lock:
            current = self._claims.get((task, input_hash))
            if current is not None and current["token"] == token:
                del self._claims[(task, input_hash)]

    def _budget(self, day: date, limit: Decimal) -> BudgetSnapshot:
        current = self._budgets.get(day)
        if current is None:
            current = BudgetSnapshot(limit, Decimal(0), Decimal(0))
            self._budgets[day] = current
        elif current.budget_eur != limit:
            raise LLMConfigurationError(
                "all workers must use the same daily LLM budget"
            )
        return current

    def reserve_budget(self, day: date, limit: Decimal, amount: Decimal) -> bool:
        with self._lock:
            current = self._budget(day, limit)
            if current.spent_eur + current.reserved_eur + amount > current.budget_eur:
                return False
            self._budgets[day] = BudgetSnapshot(
                current.budget_eur, current.spent_eur, current.reserved_eur + amount
            )
            return True

    def settle_budget(self, day: date, reserved: Decimal, actual: Decimal) -> None:
        if actual > reserved:
            raise LLMConfigurationError("actual hosted cost exceeded its reservation")
        with self._lock:
            current = self._budgets[day]
            if current.reserved_eur < reserved:
                raise CacheBusyError("budget reservation was already settled")
            self._budgets[day] = BudgetSnapshot(
                current.budget_eur,
                current.spent_eur + actual,
                current.reserved_eur - reserved,
            )

    def release_budget(self, day: date, reserved: Decimal) -> None:
        self.settle_budget(day, reserved, Decimal(0))

    def charge_abandoned(self, day: date, reserved: Decimal) -> None:
        self.settle_budget(day, reserved, reserved)

    def budget_snapshot(self, day: date) -> BudgetSnapshot | None:
        with self._lock:
            return self._budgets.get(day)

    def close(self) -> None:
        return None


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    if hasattr(row, "_asdict"):
        return dict(row._asdict())
    return dict(row)


def _first(result: Any) -> dict[str, Any]:
    try:
        row = result.one()
    except AttributeError:
        row = next(iter(result), None)
    return _row_dict(row)


def _applied(result: Any) -> tuple[bool, dict[str, Any]]:
    row = _first(result)
    value = row.get("[applied]", row.get("applied", False))
    return bool(value), row


class CassandraLLMStore:
    """Cassandra cache, lease and UTC daily-budget implementation."""

    MAX_CAS_ATTEMPTS = 25

    def __init__(self, session: Any, *, cluster: Any = None) -> None:
        self.session = session
        self.cluster = cluster

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> CassandraLLMStore:
        env = os.environ if environ is None else environ
        try:
            from cassandra.auth import PlainTextAuthProvider
            from cassandra.cluster import Cluster
        except ImportError as error:
            raise LLMConfigurationError(
                "Cassandra support requires the 'cassandra-driver' package"
            ) from error
        hosts = [
            item.strip()
            for item in env.get("CASSANDRA_HOSTS", "localhost").split(",")
            if item.strip()
        ]
        if not hosts:
            raise LLMConfigurationError(
                "CASSANDRA_HOSTS must contain at least one host"
            )
        kwargs: dict[str, Any] = {
            "port": _integer(
                "CASSANDRA_PORT",
                env.get("CASSANDRA_PORT", "9042"),
                minimum=1,
                maximum=65535,
            )
        }
        username = env.get("CASSANDRA_USERNAME")
        if username:
            kwargs["auth_provider"] = PlainTextAuthProvider(
                username=username, password=env.get("CASSANDRA_PASSWORD", "")
            )
        cluster = Cluster(hosts, **kwargs)
        try:
            session = cluster.connect(env.get("CASSANDRA_KEYSPACE", "ace"))
        except Exception:
            cluster.shutdown()
            raise
        return cls(session, cluster=cluster)

    def get_cache(self, task: str, input_hash: str) -> CacheRecord | None:
        row = _first(
            self.session.execute(
                "SELECT provider, model, answer, cost_eur, input_tokens, output_tokens, created_at "
                "FROM llm_cache WHERE task = %s AND input_hash = %s",
                (task, input_hash),
            )
        )
        if not row:
            return None
        return CacheRecord(
            answer=row["answer"],
            provider=row["provider"],
            model=row["model"],
            cost_eur=Decimal(str(row.get("cost_eur") or 0)),
            input_tokens=row.get("input_tokens"),
            output_tokens=row.get("output_tokens"),
            created_at=_utc(row["created_at"]),
        )

    def put_cache(self, task: str, input_hash: str, record: CacheRecord) -> CacheRecord:
        result = self.session.execute(
            "INSERT INTO llm_cache "
            "(task, input_hash, provider, model, answer, cost_eur, input_tokens, output_tokens, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) IF NOT EXISTS",
            (
                task,
                input_hash,
                record.provider,
                record.model,
                record.answer,
                float(record.cost_eur),
                record.input_tokens,
                record.output_tokens,
                record.created_at,
            ),
        )
        applied, _ = _applied(result)
        if applied:
            return record
        existing = self.get_cache(task, input_hash)
        if existing is None:
            raise CacheBusyError(
                "cache insert lost contention but no answer was visible"
            )
        return existing

    def try_claim(
        self,
        task: str,
        input_hash: str,
        token: uuid.UUID,
        now: datetime,
        claim_until: datetime,
    ) -> ClaimAttempt:
        result = self.session.execute(
            "INSERT INTO llm_cache_claims "
            "(task, input_hash, claim_token, claim_until, reserved_eur) "
            "VALUES (%s, %s, %s, %s, %s) IF NOT EXISTS",
            (task, input_hash, token, claim_until, Decimal(0)),
        )
        applied, row = _applied(result)
        if applied:
            return ClaimAttempt(True, claim_until)
        old_token = row.get("claim_token")
        old_until = _utc(row["claim_until"])
        if old_until > now:
            return ClaimAttempt(False, old_until)
        old_day = row.get("budget_day")
        old_reserved = Decimal(str(row.get("reserved_eur") or 0))
        takeover = self.session.execute(
            "UPDATE llm_cache_claims SET claim_token = %s, claim_until = %s, "
            "budget_day = null, reserved_eur = %s WHERE task = %s AND input_hash = %s "
            "IF claim_token = %s AND claim_until = %s",
            (
                token,
                claim_until,
                Decimal(0),
                task,
                input_hash,
                old_token,
                row["claim_until"],
            ),
        )
        won, current = _applied(takeover)
        if won:
            return ClaimAttempt(True, claim_until, old_day, old_reserved)
        wait = current.get("claim_until") or claim_until
        return ClaimAttempt(False, _utc(wait))

    def renew_claim(
        self, task: str, input_hash: str, token: uuid.UUID, claim_until: datetime
    ) -> bool:
        result = self.session.execute(
            "UPDATE llm_cache_claims SET claim_until = %s WHERE task = %s AND input_hash = %s "
            "IF claim_token = %s",
            (claim_until, task, input_hash, token),
        )
        return _applied(result)[0]

    def set_claim_reservation(
        self,
        task: str,
        input_hash: str,
        token: uuid.UUID,
        budget_day: date | None,
        amount: Decimal,
    ) -> bool:
        result = self.session.execute(
            "UPDATE llm_cache_claims SET budget_day = %s, reserved_eur = %s "
            "WHERE task = %s AND input_hash = %s IF claim_token = %s",
            (budget_day, amount, task, input_hash, token),
        )
        return _applied(result)[0]

    def release_claim(self, task: str, input_hash: str, token: uuid.UUID) -> None:
        self.session.execute(
            "DELETE FROM llm_cache_claims WHERE task = %s AND input_hash = %s IF claim_token = %s",
            (task, input_hash, token),
        )

    def _budget_row(self, day: date) -> BudgetSnapshot | None:
        row = _first(
            self.session.execute(
                "SELECT budget_eur, spent_eur, reserved_eur FROM llm_daily_budget WHERE day = %s",
                (day,),
            )
        )
        if not row:
            return None
        return BudgetSnapshot(
            Decimal(str(row["budget_eur"])),
            Decimal(str(row["spent_eur"])),
            Decimal(str(row["reserved_eur"])),
        )

    def _ensure_budget(self, day: date, limit: Decimal) -> BudgetSnapshot:
        self.session.execute(
            "INSERT INTO llm_daily_budget "
            "(day, budget_eur, spent_eur, reserved_eur, updated_at) "
            "VALUES (%s, %s, %s, %s, %s) IF NOT EXISTS",
            (day, limit, Decimal(0), Decimal(0), datetime.now(UTC)),
        )
        current = self._budget_row(day)
        if current is None:
            raise CacheBusyError("daily budget row could not be initialized")
        if current.budget_eur != limit:
            raise LLMConfigurationError(
                "all workers must use the same daily LLM budget"
            )
        return current

    def reserve_budget(self, day: date, limit: Decimal, amount: Decimal) -> bool:
        for _ in range(self.MAX_CAS_ATTEMPTS):
            current = self._ensure_budget(day, limit)
            if current.spent_eur + current.reserved_eur + amount > current.budget_eur:
                return False
            result = self.session.execute(
                "UPDATE llm_daily_budget SET reserved_eur = %s, updated_at = %s WHERE day = %s "
                "IF budget_eur = %s AND spent_eur = %s AND reserved_eur = %s",
                (
                    current.reserved_eur + amount,
                    datetime.now(UTC),
                    day,
                    current.budget_eur,
                    current.spent_eur,
                    current.reserved_eur,
                ),
            )
            if _applied(result)[0]:
                return True
        raise CacheBusyError("daily budget was too contended to reserve safely")

    def settle_budget(self, day: date, reserved: Decimal, actual: Decimal) -> None:
        if actual > reserved:
            raise LLMConfigurationError("actual hosted cost exceeded its reservation")
        for _ in range(self.MAX_CAS_ATTEMPTS):
            current = self._budget_row(day)
            if current is None or current.reserved_eur < reserved:
                raise CacheBusyError("budget reservation is missing or already settled")
            result = self.session.execute(
                "UPDATE llm_daily_budget SET spent_eur = %s, reserved_eur = %s, updated_at = %s "
                "WHERE day = %s IF spent_eur = %s AND reserved_eur = %s",
                (
                    current.spent_eur + actual,
                    current.reserved_eur - reserved,
                    datetime.now(UTC),
                    day,
                    current.spent_eur,
                    current.reserved_eur,
                ),
            )
            if _applied(result)[0]:
                return
        raise CacheBusyError("daily budget was too contended to settle safely")

    def release_budget(self, day: date, reserved: Decimal) -> None:
        self.settle_budget(day, reserved, Decimal(0))

    def charge_abandoned(self, day: date, reserved: Decimal) -> None:
        if reserved:
            self.settle_budget(day, reserved, reserved)

    def budget_snapshot(self, day: date) -> BudgetSnapshot | None:
        return self._budget_row(day)

    def close(self) -> None:
        if self.cluster is not None:
            self.cluster.shutdown()


class OllamaProvider:
    name = "local"

    def __init__(
        self, *, url: str, model: str, timeout: float, client: Any = None
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        if client is None:
            try:
                import httpx
            except ImportError as error:
                raise LLMConfigurationError(
                    "local LLM support requires the 'httpx' package"
                ) from error
            client = httpx.Client(timeout=timeout)
        self.client = client

    def count_input_tokens(self, request: LLMRequest[Any]) -> int:
        return 0

    def generate(self, request: LLMRequest[Any]) -> ProviderResponse:
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        try:
            response = self.client.post(
                f"{self.url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "format": request.response_model.model_json_schema(),
                    "options": {"temperature": 0, "num_predict": request.max_tokens},
                },
            )
            status = getattr(response, "status_code", 200)
            if status >= 400:
                raise ProviderAttemptError(
                    f"Ollama returned HTTP {status}",
                    retryable=status in {408, 409, 429} or status >= 500,
                    ambiguous_charge=False,
                )
            body = response.json()
            return ProviderResponse(
                answer=body["message"]["content"],
                input_tokens=int(body.get("prompt_eval_count", 0)),
                output_tokens=int(body.get("eval_count", 0)),
            )
        except ProviderAttemptError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProviderAttemptError(
                "Ollama returned an invalid response envelope", retryable=True
            ) from error
        except Exception as error:
            name = type(error).__name__.lower()
            retryable = "timeout" in name or "connect" in name or "network" in name
            raise ProviderAttemptError(
                f"Ollama request failed: {type(error).__name__}", retryable=retryable
            ) from error

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            close()


class AnthropicProvider:
    name = "hosted"

    def __init__(
        self, *, api_key: str, model: str, timeout: float, client: Any = None
    ) -> None:
        self.model = model
        if client is None:
            try:
                import anthropic
            except ImportError as error:
                raise LLMConfigurationError(
                    "hosted LLM support requires the 'anthropic' package"
                ) from error
            client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self.client = client

    @staticmethod
    def _messages(request: LLMRequest[Any]) -> list[dict[str, str]]:
        return [{"role": "user", "content": request.prompt}]

    @staticmethod
    def _output_config(request: LLMRequest[Any]) -> dict[str, Any]:
        return {
            "format": {
                "type": "json_schema",
                "schema": request.response_model.model_json_schema(),
            }
        }

    @staticmethod
    def _classify(error: Exception) -> ProviderAttemptError:
        status = getattr(error, "status_code", None)
        retryable = status in {408, 409, 429} or (
            isinstance(status, int) and status >= 500
        )
        name = type(error).__name__.lower()
        if status is None and any(
            part in name for part in ("timeout", "connection", "network")
        ):
            retryable = True
        ambiguous = retryable and status not in {409, 429}
        return ProviderAttemptError(
            f"Anthropic request failed: {type(error).__name__}",
            retryable=retryable,
            ambiguous_charge=ambiguous,
        )

    def count_input_tokens(self, request: LLMRequest[Any]) -> int:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(request),
            "output_config": self._output_config(request),
        }
        if request.system_prompt:
            kwargs["system"] = request.system_prompt
        try:
            response = self.client.messages.count_tokens(**kwargs)
            return int(response.input_tokens)
        except Exception as error:
            raise self._classify(error) from error

    def generate(self, request: LLMRequest[Any]) -> ProviderResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "messages": self._messages(request),
            "output_config": self._output_config(request),
        }
        if request.system_prompt:
            kwargs["system"] = request.system_prompt
        try:
            response = self.client.messages.create(**kwargs)
            answer = next(
                block.text
                for block in response.content
                if getattr(block, "type", None) == "text"
            )
            return ProviderResponse(
                answer=answer,
                input_tokens=int(response.usage.input_tokens),
                output_tokens=int(response.usage.output_tokens),
            )
        except (StopIteration, AttributeError, TypeError, ValueError) as error:
            raise ProviderAttemptError(
                "Anthropic returned an invalid response envelope",
                retryable=True,
                ambiguous_charge=True,
            ) from error
        except Exception as error:
            raise self._classify(error) from error

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close:
            close()


class RecordedProvider:
    """Exact-hash JSON fixture provider for deterministic offline tests."""

    def __init__(self, path: str | Path, *, provider: str, model: str) -> None:
        self.name = provider
        self.model = model
        with Path(path).open(encoding="utf-8") as file:
            payload = json.load(file)
        recordings = payload.get("recordings")
        if not isinstance(recordings, dict):
            raise LLMConfigurationError(
                "recording file must contain an object named 'recordings'"
            )
        self.recordings: dict[str, dict[str, Any]] = recordings
        self.calls = 0

    def _recording(self, request: LLMRequest[Any]) -> dict[str, Any]:
        try:
            return self.recordings[request.request_hash]
        except KeyError as error:
            raise RecordingNotFoundError(
                f"no recorded answer for request {request.request_hash[:12]}"
            ) from error

    def count_input_tokens(self, request: LLMRequest[Any]) -> int:
        return int(self._recording(request).get("input_tokens", 0))

    def generate(self, request: LLMRequest[Any]) -> ProviderResponse:
        self.calls += 1
        recording = self._recording(request)
        answer = recording.get("answer")
        if (
            not isinstance(answer, (dict, list, str, int, float, bool))
            and answer is not None
        ):
            raise LLMConfigurationError("recorded answer is not JSON serializable")
        return ProviderResponse(
            answer=_canonical_json(answer),
            input_tokens=int(recording.get("input_tokens", 0)),
            output_tokens=int(recording.get("output_tokens", 0)),
        )

    def close(self) -> None:
        return None


def build_provider(settings: LLMSettings) -> Provider:
    if settings.provider == "local":
        return OllamaProvider(
            url=settings.local_url,
            model=settings.local_model,
            timeout=settings.timeout_seconds,
        )
    return AnthropicProvider(
        api_key=cast(str, settings.api_key),
        model=settings.hosted_model,
        timeout=settings.timeout_seconds,
    )


class LLMClient:
    def __init__(
        self,
        settings: LLMSettings,
        store: LLMStore,
        provider: Provider,
        *,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        random_value: Callable[[], float] | None = None,
    ) -> None:
        if provider.name != settings.provider:
            raise LLMConfigurationError("injected provider does not match LLM_PROVIDER")
        expected_model = (
            settings.local_model
            if settings.provider == "local"
            else settings.hosted_model
        )
        if provider.model != expected_model:
            raise LLMConfigurationError(
                "injected provider model does not match configured model"
            )
        self.settings = settings
        self.store = store
        self.provider = provider
        self._now = now or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleep or time.sleep
        self._random = random_value or random.random

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        store: LLMStore | None = None,
        provider: Provider | None = None,
    ) -> LLMClient:
        settings = LLMSettings.from_env(environ)
        return cls(
            settings,
            store or CassandraLLMStore.from_env(environ),
            provider or build_provider(settings),
        )

    def _validated_cache(
        self, record: CacheRecord, response_model: type[T]
    ) -> LLMResult[T]:
        try:
            value = response_model.model_validate_json(record.answer)
        except (ValidationError, ValueError) as error:
            raise LLMResponseError(
                "cached LLM answer does not match the requested schema"
            ) from error
        return LLMResult(
            value=value,
            provider=record.provider,
            model=record.model,
            cached=True,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cost_eur=record.cost_eur,
        )

    def _reservation(self, input_tokens: int, max_tokens: int) -> Decimal:
        input_rate = cast(Decimal, self.settings.hosted_input_eur_per_mtok)
        output_rate = cast(Decimal, self.settings.hosted_output_eur_per_mtok)
        amount = (
            Decimal(input_tokens) * input_rate + Decimal(max_tokens) * output_rate
        ) / PER_MILLION
        return amount.quantize(MICRO_EUR, rounding=ROUND_CEILING)

    def _actual_cost(self, response: ProviderResponse) -> Decimal:
        if self.settings.provider == "local":
            return Decimal(0)
        input_rate = cast(Decimal, self.settings.hosted_input_eur_per_mtok)
        output_rate = cast(Decimal, self.settings.hosted_output_eur_per_mtok)
        amount = (
            Decimal(response.input_tokens) * input_rate
            + Decimal(response.output_tokens) * output_rate
        ) / PER_MILLION
        return amount.quantize(MICRO_EUR, rounding=ROUND_CEILING)

    def _clear_and_settle(
        self,
        request: LLMRequest[Any],
        token: uuid.UUID,
        budget_day: date,
        reserved: Decimal,
        actual: Decimal,
    ) -> None:
        if not self.store.set_claim_reservation(
            request.task, request.request_hash, token, None, Decimal(0)
        ):
            raise CacheBusyError("request lease was lost while settling the budget")
        self.store.settle_budget(budget_day, reserved, actual)

    def _clear_and_release(
        self,
        request: LLMRequest[Any],
        token: uuid.UUID,
        budget_day: date,
        reserved: Decimal,
        *,
        charge_full: bool,
    ) -> None:
        if not self.store.set_claim_reservation(
            request.task, request.request_hash, token, None, Decimal(0)
        ):
            raise CacheBusyError("request lease was lost while releasing the budget")
        if charge_full:
            self.store.charge_abandoned(budget_day, reserved)
        else:
            self.store.release_budget(budget_day, reserved)

    def generate(
        self,
        *,
        task: str,
        prompt_version: str,
        system_prompt: str,
        prompt: str,
        response_model: type[T],
        max_tokens: int | None = None,
    ) -> LLMResult[T]:
        if not TASK_RE.fullmatch(task):
            raise LLMConfigurationError(
                "task must be lower snake_case and at most 100 characters"
            )
        if not prompt_version.strip() or not prompt.strip():
            raise LLMConfigurationError("prompt_version and prompt must not be empty")
        if not isinstance(response_model, type) or not issubclass(
            response_model, BaseModel
        ):
            raise LLMConfigurationError(
                "response_model must be a Pydantic BaseModel type"
            )
        token_limit = self.settings.max_tokens if max_tokens is None else max_tokens
        if not 1 <= token_limit <= self.settings.max_tokens:
            raise LLMConfigurationError(
                f"max_tokens must be between 1 and configured limit {self.settings.max_tokens}"
            )
        digest = request_hash(
            task=task,
            prompt_version=prompt_version,
            provider=self.provider.name,
            model=self.provider.model,
            system_prompt=system_prompt,
            prompt=prompt,
            response_model=response_model,
            max_tokens=token_limit,
        )
        request = LLMRequest(
            task=task,
            prompt_version=prompt_version,
            system_prompt=system_prompt,
            prompt=prompt,
            response_model=response_model,
            max_tokens=token_limit,
            request_hash=digest,
        )
        cached = self.store.get_cache(task, digest)
        if cached is not None:
            LOG.info(
                "LLM cache hit task=%s provider=%s model=%s hash=%s",
                task,
                self.provider.name,
                self.provider.model,
                digest[:12],
            )
            return self._validated_cache(cached, response_model)
        LOG.info(
            "LLM cache miss task=%s provider=%s model=%s hash=%s",
            task,
            self.provider.name,
            self.provider.model,
            digest[:12],
        )

        claim_token = uuid.uuid4()
        wait_deadline = self._monotonic() + self.settings.cache_lease_seconds
        while True:
            now = _utc(self._now())
            attempt = self.store.try_claim(
                task,
                digest,
                claim_token,
                now,
                now + timedelta(seconds=self.settings.cache_lease_seconds),
            )
            if attempt.acquired:
                if attempt.abandoned_budget_day and attempt.abandoned_reservation_eur:
                    self.store.charge_abandoned(
                        attempt.abandoned_budget_day, attempt.abandoned_reservation_eur
                    )
                break
            cached = self.store.get_cache(task, digest)
            if cached is not None:
                return self._validated_cache(cached, response_model)
            if self._monotonic() >= wait_deadline:
                raise CacheBusyError(f"timed out waiting for request {digest[:12]}")
            self._sleep(self.settings.cache_poll_seconds)

        try:
            cached = self.store.get_cache(task, digest)
            if cached is not None:
                return self._validated_cache(cached, response_model)
            last_error: Exception | None = None
            for attempt_number in range(1, self.settings.max_attempts + 1):
                lease_until = _utc(self._now()) + timedelta(
                    seconds=self.settings.cache_lease_seconds
                )
                if not self.store.renew_claim(task, digest, claim_token, lease_until):
                    raise CacheBusyError(
                        "request lease was lost before the provider call"
                    )
                reserved = Decimal(0)
                budget_day: date | None = None
                try:
                    if self.settings.provider == "hosted":
                        try:
                            counted_tokens = self.provider.count_input_tokens(request)
                        except (ProviderAttemptError, RecordingNotFoundError):
                            raise
                        except Exception as error:
                            raise ProviderAttemptError(
                                f"token count failed: {type(error).__name__}",
                                retryable=False,
                            ) from error
                        if counted_tokens < 0:
                            raise ProviderAttemptError(
                                "provider returned a negative input-token count",
                                retryable=True,
                            )
                        reserved = self._reservation(counted_tokens, token_limit)
                        budget_day = _utc(self._now()).date()
                        if not self.store.reserve_budget(
                            budget_day, self.settings.daily_budget_eur, reserved
                        ):
                            raise BudgetExceededError(
                                f"hosted call would exceed the UTC daily budget for {budget_day}"
                            )
                        if not self.store.set_claim_reservation(
                            task, digest, claim_token, budget_day, reserved
                        ):
                            self.store.release_budget(budget_day, reserved)
                            raise CacheBusyError(
                                "request lease was lost after reserving budget"
                            )
                    try:
                        response = self.provider.generate(request)
                    except (ProviderAttemptError, RecordingNotFoundError):
                        raise
                    except Exception as error:
                        raise ProviderAttemptError(
                            f"provider failed unexpectedly: {type(error).__name__}",
                            retryable=False,
                            ambiguous_charge=budget_day is not None,
                        ) from error
                    if response.input_tokens < 0 or response.output_tokens < 0:
                        raise ProviderAttemptError(
                            "provider returned negative token usage",
                            retryable=True,
                            ambiguous_charge=budget_day is not None,
                        )
                    try:
                        value = response_model.model_validate_json(response.answer)
                    except (ValidationError, ValueError) as error:
                        if budget_day is not None:
                            self._clear_and_settle(
                                request,
                                claim_token,
                                budget_day,
                                reserved,
                                self._actual_cost(response),
                            )
                        last_error = LLMResponseError(
                            "provider returned JSON that did not match the requested schema"
                        )
                        if attempt_number == self.settings.max_attempts:
                            raise last_error from error
                        self._backoff(attempt_number)
                        continue
                    cost = self._actual_cost(response)
                    if budget_day is not None:
                        self._clear_and_settle(
                            request, claim_token, budget_day, reserved, cost
                        )
                    record = CacheRecord(
                        answer=value.model_dump_json(),
                        provider=self.provider.name,
                        model=self.provider.model,
                        cost_eur=cost,
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                        created_at=_utc(self._now()),
                    )
                    stored = self.store.put_cache(task, digest, record)
                    validated = self._validated_cache(stored, response_model)
                    LOG.info(
                        "LLM complete task=%s provider=%s model=%s hash=%s attempt=%d input_tokens=%d output_tokens=%d cost_eur=%s",
                        task,
                        self.provider.name,
                        self.provider.model,
                        digest[:12],
                        attempt_number,
                        response.input_tokens,
                        response.output_tokens,
                        cost,
                    )
                    return LLMResult(
                        value=validated.value,
                        provider=validated.provider,
                        model=validated.model,
                        cached=False,
                        input_tokens=validated.input_tokens,
                        output_tokens=validated.output_tokens,
                        cost_eur=validated.cost_eur,
                    )
                except BudgetExceededError:
                    raise
                except RecordingNotFoundError:
                    raise
                except ProviderAttemptError as error:
                    if budget_day is not None and reserved:
                        self._clear_and_release(
                            request,
                            claim_token,
                            budget_day,
                            reserved,
                            charge_full=error.ambiguous_charge,
                        )
                    last_error = error
                    LOG.warning(
                        "LLM provider error task=%s provider=%s model=%s hash=%s attempt=%d error=%s retryable=%s",
                        task,
                        self.provider.name,
                        self.provider.model,
                        digest[:12],
                        attempt_number,
                        type(error).__name__,
                        error.retryable,
                    )
                    if (
                        not error.retryable
                        or attempt_number == self.settings.max_attempts
                    ):
                        raise LLMProviderError(str(error)) from error
                    self._backoff(attempt_number)
            raise LLMProviderError(str(last_error or "provider attempts exhausted"))
        finally:
            self.store.release_claim(task, digest, claim_token)

    def _backoff(self, attempt_number: int) -> None:
        base = 0.5 * (2 ** (attempt_number - 1))
        self._sleep(base + self._random() * min(0.25, base / 2))

    def close(self) -> None:
        try:
            self.provider.close()
        finally:
            self.store.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


__all__ = [
    "AnthropicProvider",
    "BudgetExceededError",
    "CacheBusyError",
    "CassandraLLMStore",
    "InMemoryLLMStore",
    "LLMClient",
    "LLMConfigurationError",
    "LLMError",
    "LLMProviderError",
    "LLMResponseError",
    "LLMResult",
    "LLMSettings",
    "OllamaProvider",
    "RecordedProvider",
    "RecordingNotFoundError",
    "request_hash",
]
