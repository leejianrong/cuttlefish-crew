"""An OpenRouter-backed provider for cuttlefish's own reasoning calls (QUESTIONS.md Q11).

Q11 always named "claude, or an OpenAI-compatible endpoint" as the two real choices;
OpenRouter is that second choice, reached through the OpenAI-compatible chat completions
API OpenRouter exposes at ``https://openrouter.ai/api/v1``. It is the default provider
(``cli.py``'s ``DEFAULT_LLM_PROVIDER``): one key covers many upstream models rather than
locking cuttlefish to a single vendor's own SDK and credential.

Unlike ``ClaudeLlmProvider``, the credential can't be left entirely to the client's own
env-var default — the OpenAI SDK looks for ``OPENAI_API_KEY``, not ``OPENROUTER_API_KEY``
— so this module reads it once, itself, and hands it straight to the client. It is never
logged (ADR-0004's redactor also has ``OPENROUTER_API_KEY`` in its known-secret list).
"""

from __future__ import annotations

import os

import openai

from cuttlefish.llm.provider import LlmResponse

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
DEFAULT_MODEL = "openrouter/auto"
DEFAULT_MAX_TOKENS = 4096


class MissingApiKeyError(RuntimeError):
    """`OPENROUTER_API_KEY` isn't set (checked before the first call, not mid-task)."""


class OpenRouterLlmProvider:
    """One prompt in, one response out, over OpenRouter's OpenAI-compatible API."""

    def __init__(self, *, model: str = DEFAULT_MODEL, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        api_key = os.environ.get(OPENROUTER_API_KEY_ENV)
        if not api_key:
            raise MissingApiKeyError(
                f"{OPENROUTER_API_KEY_ENV} is not set. cuttlefish needs it only for its own "
                "handover summaries (the coding agent has its own credential): set it, or set "
                "CUTTLEFISH_LLM_PROVIDER=replay for placeholder summaries."
            )
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
        self._model = model
        self._max_tokens = max_tokens

    async def complete(self, prompt: str) -> LlmResponse:
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        choice = response.choices[0]
        text = choice.message.content or ""
        usage = response.usage
        return LlmResponse(
            model=response.model,
            text=text,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )
