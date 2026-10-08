"""Unit: the OpenRouter provider's model choice and what it reads back, with no network."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from cuttlefish.llm.openrouter import DEFAULT_MODEL, MODEL_ENV, OpenRouterLlmProvider


class _Usage:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)
        self.prompt_tokens = fields.get("prompt_tokens")
        self.completion_tokens = fields.get("completion_tokens")

    def model_dump(self) -> dict[str, Any]:
        return dict(self.__dict__)


class _Completions:
    def __init__(self, usage: _Usage | None, text: str | None = "a report") -> None:
        self.calls: list[dict[str, Any]] = []
        self._usage, self._text = usage, text

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        choice = SimpleNamespace(message=SimpleNamespace(content=self._text))
        return SimpleNamespace(model=kwargs["model"], choices=[choice], usage=self._usage)


def _provider(monkeypatch: pytest.MonkeyPatch, completions: _Completions) -> OpenRouterLlmProvider:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    provider = OpenRouterLlmProvider()
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))  # type: ignore[assignment]
    return provider


def test_the_default_is_a_pinned_small_model_not_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MODEL_ENV, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert OpenRouterLlmProvider()._model == DEFAULT_MODEL != "openrouter/auto"


def test_the_model_can_be_chosen_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODEL_ENV, " openai/gpt-oss-20b ")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert OpenRouterLlmProvider()._model == "openai/gpt-oss-20b"
    assert OpenRouterLlmProvider(model="x/y")._model == "x/y"  # an explicit one wins


async def test_it_asks_for_the_cost_and_low_reasoning_and_reads_the_cost_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = _Usage(prompt_tokens=1200, completion_tokens=300, cost=0.00031)
    completions = _Completions(usage)
    response = await _provider(monkeypatch, completions).complete("p")
    assert completions.calls[0]["extra_body"] == {
        "reasoning": {"effort": "low"},
        "usage": {"include": True},
    }
    assert (response.input_tokens, response.output_tokens, response.cost_usd) == (
        1200,
        300,
        0.00031,
    )


async def test_a_missing_cost_stays_missing_never_estimated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = _Usage(prompt_tokens=10, completion_tokens=5)
    response = await _provider(monkeypatch, _Completions(usage)).complete("p")
    assert response.cost_usd is None
    assert (await _provider(monkeypatch, _Completions(None, text=None)).complete("p")).text == ""
