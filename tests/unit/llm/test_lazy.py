from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish.config import resolve_llm_provider, shared_openrouter_key
from cuttlefish.llm.lazy import LazyLlmProvider
from cuttlefish.llm.openrouter import MissingApiKeyError, OpenRouterLlmProvider
from cuttlefish.llm.provider import LlmProvider, LlmResponse
from cuttlefish.secrets.store import SHARED_SCOPE, SecretsStore, generate_key


class _Fake:
    async def complete(self, prompt: str) -> LlmResponse:
        return LlmResponse(model="fake", text=prompt)


async def test_factory_runs_once_on_first_call() -> None:
    calls = 0

    def factory() -> LlmProvider:
        nonlocal calls
        calls += 1
        return _Fake()

    provider = LazyLlmProvider(factory)
    assert calls == 0
    assert (await provider.complete("a")).text == "a"
    await provider.complete("b")
    assert calls == 1


def test_default_provider_does_not_need_a_key_at_resolve_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CUTTLEFISH_LLM_PROVIDER", raising=False)
    resolve_llm_provider()


async def test_missing_key_fails_on_first_call_with_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CUTTLEFISH_LLM_PROVIDER", raising=False)
    provider = resolve_llm_provider()
    with pytest.raises(MissingApiKeyError, match="CUTTLEFISH_LLM_PROVIDER=replay"):
        await provider.complete("x")


def _store(tmp_path: Path) -> SecretsStore:
    return SecretsStore.open(tmp_path / "s.db", key=generate_key())


def test_with_no_env_key_the_summaries_use_the_shared_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    store = _store(tmp_path)
    store.set(SHARED_SCOPE, "OPENROUTER_API_KEY", "sk-or-shared-canary-value")
    assert shared_openrouter_key(store) == "sk-or-shared-canary-value"
    provider = OpenRouterLlmProvider(fallback_key=lambda: shared_openrouter_key(store))
    assert provider is not None  # built: no MissingApiKeyError


def test_the_environment_key_wins_over_the_shared_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env-canary-value")
    asked: list[bool] = []
    OpenRouterLlmProvider(fallback_key=lambda: asked.append(True) or "unused")
    assert asked == []  # the store was not even consulted


def test_a_projects_own_openrouter_secret_is_not_used_for_cuttlefishs_summaries(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.set("demo", "OPENROUTER_API_KEY", "sk-or-agent-canary-value")
    assert shared_openrouter_key(store) is None
    assert shared_openrouter_key(None) is None


async def test_with_neither_the_error_names_the_dashboard_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CUTTLEFISH_LLM_PROVIDER", raising=False)
    provider = resolve_llm_provider(_store(tmp_path))
    with pytest.raises(MissingApiKeyError, match="shared OPENROUTER_API_KEY under Secrets"):
        await provider.complete("x")


def test_the_client_is_built_with_the_shared_secret_when_the_environment_has_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CUTTLEFISH_LLM_PROVIDER", raising=False)
    keys: list[str] = []

    class Spy:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            keys.append(api_key)

    monkeypatch.setattr("cuttlefish.llm.openrouter.openai.AsyncOpenAI", Spy)
    store = _store(tmp_path)
    provider = resolve_llm_provider(store)
    assert isinstance(provider, LazyLlmProvider)
    # Set after the provider was resolved: the key is read when the first summary is due.
    store.set(SHARED_SCOPE, "OPENROUTER_API_KEY", "sk-or-shared-canary-value")
    provider._factory()
    assert keys == ["sk-or-shared-canary-value"]
