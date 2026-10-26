from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from ace.llm import (
    AnthropicProvider,
    BudgetExceededError,
    CacheRecord,
    InMemoryLLMStore,
    LLMClient,
    LLMConfigurationError,
    LLMProviderError,
    LLMResponseError,
    LLMSettings,
    OllamaProvider,
    ProviderAttemptError,
    ProviderResponse,
    RecordedProvider,
    RecordingNotFoundError,
    build_provider,
    request_hash,
)
from pydantic import BaseModel

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


class Answer(BaseModel):
    value: str


class ScriptedProvider:
    def __init__(self, outcomes, *, name="local", model="qwen3:4b", input_tokens=4):
        self.name = name
        self.model = model
        self.outcomes = list(outcomes)
        self.input_tokens = input_tokens
        self.calls = 0
        self.count_calls = 0

    def count_input_tokens(self, request):
        self.count_calls += 1
        return self.input_tokens

    def generate(self, request):
        self.calls += 1
        if not self.outcomes:
            raise AssertionError("provider called more times than scripted")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def close(self):
        pass


def local_settings(**changes):
    values = {
        "provider": "local",
        "local_model": "qwen3:4b",
        "timeout_seconds": 1,
        "max_attempts": 3,
        "max_tokens": 20,
        "cache_lease_seconds": 10,
        "cache_poll_seconds": 0.005,
    }
    values.update(changes)
    return LLMSettings(**values)


def hosted_settings(**changes):
    values = {
        "provider": "hosted",
        "hosted_model": "claude-haiku-5-5",
        "api_key": "test-key",
        "daily_budget_eur": Decimal(1),
        "hosted_input_eur_per_mtok": Decimal(50000),
        "hosted_output_eur_per_mtok": Decimal(50000),
        "timeout_seconds": 1,
        "max_attempts": 3,
        "max_tokens": 10,
        "cache_lease_seconds": 10,
        "cache_poll_seconds": 0.005,
    }
    values.update(changes)
    return LLMSettings(**values)


def make_client(settings, provider, store=None, **kwargs):
    return LLMClient(
        settings,
        store or InMemoryLLMStore(),
        provider,
        now=lambda: NOW,
        sleep=kwargs.pop("sleep", lambda _seconds: None),
        random_value=lambda: 0,
        **kwargs,
    )


def generate(client, *, prompt="hello", version="v1"):
    return client.generate(
        task="skill_extraction",
        prompt_version=version,
        system_prompt="return JSON",
        prompt=prompt,
        response_model=Answer,
        max_tokens=10,
    )


def good(value="ok", *, input_tokens=4, output_tokens=2):
    return ProviderResponse(
        json.dumps({"value": value}),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def test_settings_select_local_defaults_and_hosted_overrides():
    local = LLMSettings.from_env({})
    assert local.provider == "local"
    assert local.local_model == "qwen3:4b"

    hosted = LLMSettings.from_env(
        {
            "LLM_PROVIDER": "hosted",
            "LLM_API_KEY": "secret",
            "LLM_HOSTED_MODEL": "custom-haiku",
            "LLM_HOSTED_INPUT_EUR_PER_MTOK": "0.1",
            "LLM_HOSTED_OUTPUT_EUR_PER_MTOK": "0.5",
        }
    )
    assert hosted.provider == "hosted"
    assert hosted.hosted_model == "custom-haiku"

    local_provider = build_provider(local)
    hosted_provider = build_provider(hosted)
    try:
        assert isinstance(local_provider, OllamaProvider)
        assert isinstance(hosted_provider, AnthropicProvider)
    finally:
        local_provider.close()
        hosted_provider.close()


@pytest.mark.parametrize(
    "env, message",
    [
        ({"LLM_PROVIDER": "other"}, "LLM_PROVIDER"),
        ({"LLM_PROVIDER": "hosted"}, "LLM_API_KEY"),
        (
            {"LLM_PROVIDER": "hosted", "LLM_API_KEY": "x"},
            "LLM_HOSTED_INPUT_EUR_PER_MTOK",
        ),
        ({"LLM_MAX_ATTEMPTS": "0"}, "LLM_MAX_ATTEMPTS"),
        ({"LLM_DAILY_BUDGET_EUR": "nan"}, "LLM_DAILY_BUDGET_EUR"),
        (
            {
                "LLM_PROVIDER": "hosted",
                "LLM_API_KEY": "x",
                "LLM_HOSTED_INPUT_EUR_PER_MTOK": "0",
                "LLM_HOSTED_OUTPUT_EUR_PER_MTOK": "1",
            },
            "token rates",
        ),
    ],
)
def test_settings_reject_invalid_configuration(env, message):
    with pytest.raises(LLMConfigurationError, match=message):
        LLMSettings.from_env(env)


def test_cache_hit_skips_provider_and_budget():
    store = InMemoryLLMStore()
    provider = ScriptedProvider([good()])
    client = make_client(local_settings(), provider, store)
    first = generate(client)
    second = generate(client)

    assert first.cached is False
    assert second.cached is True
    assert second.value.value == "ok"
    assert provider.calls == 1


def test_hash_is_provider_model_and_schema_specific():
    base = {
        "task": "skill_extraction",
        "prompt_version": "v1",
        "system_prompt": "system",
        "prompt": "input",
        "response_model": Answer,
        "max_tokens": 10,
    }
    local = request_hash(provider="local", model="qwen3:4b", **base)
    hosted = request_hash(provider="hosted", model="claude-haiku-5-5", **base)
    other_model = request_hash(provider="local", model="qwen3:8b", **base)
    assert len(local) == 64
    assert len({local, hosted, other_model}) == 3


def test_two_concurrent_clients_make_one_provider_call():
    store = InMemoryLLMStore()
    started = threading.Event()
    release = threading.Event()

    class BlockingProvider(ScriptedProvider):
        def generate(self, request):
            self.calls += 1
            started.set()
            assert release.wait(2)
            return good()

    provider = BlockingProvider([])
    settings = local_settings(cache_poll_seconds=0.001)
    clients = [
        LLMClient(
            settings,
            store,
            provider,
            now=lambda: NOW,
            sleep=time.sleep,
            random_value=lambda: 0,
        )
        for _ in range(2)
    ]
    results = []
    errors = []

    def worker(client):
        try:
            results.append(generate(client))
        except Exception as error:  # noqa: BLE001  # pragma: no cover - thread diagnostics
            errors.append(error)

    first = threading.Thread(target=worker, args=(clients[0],))
    second = threading.Thread(target=worker, args=(clients[1],))
    first.start()
    assert started.wait(1)
    second.start()
    time.sleep(0.02)
    release.set()
    first.join(2)
    second.join(2)

    assert not errors
    assert provider.calls == 1
    assert sorted(result.cached for result in results) == [False, True]


def test_expired_claim_is_taken_over_and_reservation_is_charged():
    store = InMemoryLLMStore()
    old_token = uuid.uuid4()
    day = NOW.date()
    assert store.reserve_budget(day, Decimal(2), Decimal("0.5"))
    claim = store.try_claim(
        "skill_extraction", "hash", old_token, NOW, NOW + timedelta(seconds=1)
    )
    assert claim.acquired
    assert store.set_claim_reservation(
        "skill_extraction", "hash", old_token, day, Decimal("0.5")
    )

    takeover = store.try_claim(
        "skill_extraction",
        "hash",
        uuid.uuid4(),
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=12),
    )
    assert takeover.acquired
    assert takeover.abandoned_reservation_eur == Decimal("0.5")
    store.charge_abandoned(day, takeover.abandoned_reservation_eur)
    snapshot = store.budget_snapshot(day)
    assert snapshot.spent_eur == Decimal("0.5")
    assert snapshot.reserved_eur == 0


def test_local_call_costs_zero_and_does_not_create_budget_row():
    store = InMemoryLLMStore()
    result = generate(make_client(local_settings(), ScriptedProvider([good()]), store))
    assert result.cost_eur == 0
    assert store.budget_snapshot(NOW.date()) is None


def test_hosted_call_reserves_maximum_and_settles_actual():
    store = InMemoryLLMStore()
    provider = ScriptedProvider(
        [good(input_tokens=4, output_tokens=2)],
        name="hosted",
        model="claude-haiku-5-5",
        input_tokens=4,
    )
    result = generate(make_client(hosted_settings(), provider, store))
    snapshot = store.budget_snapshot(NOW.date())
    assert result.cost_eur == Decimal("0.300000")
    assert snapshot.spent_eur == Decimal("0.300000")
    assert snapshot.reserved_eur == 0


def test_budget_boundary_blocks_new_call_but_allows_cache_hit():
    store = InMemoryLLMStore()
    provider = ScriptedProvider(
        [good(input_tokens=10, output_tokens=10), good("second")],
        name="hosted",
        model="claude-haiku-5-5",
        input_tokens=10,
    )
    client = make_client(hosted_settings(), provider, store)
    first = generate(client, prompt="first")
    assert first.cost_eur == Decimal("1.000000")
    assert generate(client, prompt="first").cached
    with pytest.raises(BudgetExceededError):
        generate(client, prompt="second")
    assert provider.calls == 1


def test_concurrent_budget_reservations_never_exceed_limit():
    store = InMemoryLLMStore()
    barrier = threading.Barrier(3)
    results = []

    def reserve():
        barrier.wait()
        results.append(store.reserve_budget(NOW.date(), Decimal(1), Decimal("0.6")))

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert sorted(results) == [False, True]
    assert store.budget_snapshot(NOW.date()).reserved_eur == Decimal("0.6")


def test_retryable_error_retries_and_permanent_error_stops():
    sleeps = []
    retrying = ScriptedProvider([ProviderAttemptError("busy", retryable=True), good()])
    result = generate(make_client(local_settings(), retrying, sleep=sleeps.append))
    assert result.value.value == "ok"
    assert retrying.calls == 2
    assert sleeps == [0.5]

    permanent = ScriptedProvider(
        [ProviderAttemptError("auth", retryable=False), good()]
    )
    with pytest.raises(LLMProviderError, match="auth"):
        generate(make_client(local_settings(), permanent))
    assert permanent.calls == 1


def test_invalid_json_retries_and_is_never_cached():
    store = InMemoryLLMStore()
    provider = ScriptedProvider([ProviderResponse("not-json", 1, 1), good("fixed")])
    result = generate(make_client(local_settings(), provider, store))
    assert result.value.value == "fixed"
    assert provider.calls == 2

    failing = ScriptedProvider([ProviderResponse("{}", 1, 1)] * 3)
    with pytest.raises(LLMResponseError):
        generate(make_client(local_settings(), failing, InMemoryLLMStore()))


def test_recorded_provider_exact_match_and_missing_recording():
    settings = local_settings()
    fixture = Path(__file__).with_name("recordings") / "skill_extraction.json"
    provider = RecordedProvider(fixture, provider="local", model="qwen3:4b")
    assert generate(make_client(settings, provider)).value.value == "recorded"

    missing = RecordedProvider(fixture, provider="local", model="qwen3:4b")
    with pytest.raises(RecordingNotFoundError):
        generate(make_client(settings, missing), prompt="not recorded")


def test_logs_do_not_contain_prompt_or_api_key(caplog):
    secret_prompt = "PRIVATE-JOB-TEXT"
    settings = hosted_settings(api_key="SUPER-SECRET-KEY")
    provider = ScriptedProvider(
        [good()], name="hosted", model="claude-haiku-5-5", input_tokens=4
    )
    with caplog.at_level(logging.INFO):
        generate(make_client(settings, provider), prompt=secret_prompt)
    log_text = caplog.text
    assert secret_prompt not in log_text
    assert settings.api_key not in log_text


class FakeHTTPResponse:
    status_code = 200

    def json(self):
        return {
            "message": {"content": '{"value":"ollama"}'},
            "prompt_eval_count": 3,
            "eval_count": 2,
        }


class FakeHTTPClient:
    def __init__(self):
        self.payload = None

    def post(self, url, json):
        self.url = url
        self.payload = json
        return FakeHTTPResponse()


class FakeAnthropicMessages:
    def __init__(self):
        self.count_kwargs = None
        self.create_kwargs = None

    def count_tokens(self, **kwargs):
        self.count_kwargs = kwargs
        return SimpleNamespace(input_tokens=7)

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"value":"claude"}')],
            usage=SimpleNamespace(input_tokens=7, output_tokens=3),
        )


class FakeAnthropicClient:
    def __init__(self):
        self.messages = FakeAnthropicMessages()


def test_anthropic_adapter_counts_the_structured_request_and_parses_response():
    api = FakeAnthropicClient()
    provider = AnthropicProvider(
        api_key="unused", model="claude-haiku-5-5", timeout=1, client=api
    )
    digest = request_hash(
        task="skill_extraction",
        prompt_version="v1",
        provider="hosted",
        model="claude-haiku-5-5",
        system_prompt="system",
        prompt="input",
        response_model=Answer,
        max_tokens=10,
    )
    from ace.llm import LLMRequest

    request = LLMRequest(
        "skill_extraction", "v1", "system", "input", Answer, 10, digest
    )
    assert provider.count_input_tokens(request) == 7
    response = provider.generate(request)

    assert json.loads(response.answer) == {"value": "claude"}
    assert response.input_tokens == 7
    assert response.output_tokens == 3
    assert (
        api.messages.count_kwargs["output_config"]
        == api.messages.create_kwargs["output_config"]
    )
    assert api.messages.create_kwargs["max_tokens"] == 10
    assert api.messages.create_kwargs["system"] == "system"


def test_ollama_adapter_sends_schema_and_deterministic_options():
    http = FakeHTTPClient()
    provider = OllamaProvider(
        url="http://ollama:11434", model="qwen3:4b", timeout=1, client=http
    )
    digest = request_hash(
        task="skill_extraction",
        prompt_version="v1",
        provider="local",
        model="qwen3:4b",
        system_prompt="system",
        prompt="input",
        response_model=Answer,
        max_tokens=10,
    )
    from ace.llm import LLMRequest

    response = provider.generate(
        LLMRequest("skill_extraction", "v1", "system", "input", Answer, 10, digest)
    )
    assert json.loads(response.answer) == {"value": "ollama"}
    assert http.payload["stream"] is False
    assert http.payload["think"] is False
    assert http.payload["format"] == Answer.model_json_schema()
    assert http.payload["options"] == {"temperature": 0, "num_predict": 10}


def test_cache_rejects_corrupt_answer_without_calling_provider():
    settings = local_settings()
    provider = ScriptedProvider([good()])
    store = InMemoryLLMStore()
    digest = request_hash(
        task="skill_extraction",
        prompt_version="v1",
        provider="local",
        model="qwen3:4b",
        system_prompt="return JSON",
        prompt="hello",
        response_model=Answer,
        max_tokens=10,
    )
    store.put_cache(
        "skill_extraction",
        digest,
        CacheRecord("{}", "local", "qwen3:4b", Decimal(0), 1, 1, NOW),
    )
    with pytest.raises(LLMResponseError):
        generate(make_client(settings, provider, store))
    assert provider.calls == 0
