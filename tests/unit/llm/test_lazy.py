from __future__ import annotations

import pytest

from cuttlefish.config import resolve_llm_provider
from cuttlefish.llm.lazy import LazyLlmProvider
from cuttlefish.llm.openrouter import MissingApiKeyError
from cuttlefish.llm.provider import LlmProvider, LlmResponse


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
