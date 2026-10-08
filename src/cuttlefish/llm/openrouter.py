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

import logging
import os

import openai

from cuttlefish.llm.provider import LlmResponse

_LOG = logging.getLogger(__name__)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
#: cuttlefish's own summaries (handovers) need no strong model. Pinned, not `openrouter/auto`: auto
#: picks per request and once picked a reasoning model that spent its whole output cap thinking and
#: answered nothing. A small instruct model, no reasoning, about $0.003 a summary. Override with
#: ``CUTTLEFISH_LLM_MODEL`` (any OpenRouter model id).
DEFAULT_MODEL = "qwen/qwen3-30b-a3b-instruct-2507"
MODEL_ENV = "CUTTLEFISH_LLM_MODEL"
DEFAULT_MAX_TOKENS = 8192


class MissingApiKeyError(RuntimeError):
    """`OPENROUTER_API_KEY` isn't set (checked before the first call, not mid-task)."""


class OpenRouterLlmProvider:
    """One prompt in, one response out, over OpenRouter's OpenAI-compatible API."""

    def __init__(self, *, model: str | None = None, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        api_key = os.environ.get(OPENROUTER_API_KEY_ENV)
        if not api_key:
            raise MissingApiKeyError(
                f"{OPENROUTER_API_KEY_ENV} is not set. cuttlefish needs it only for its own "
                "handover summaries (the coding agent has its own credential): set it, or set "
                "CUTTLEFISH_LLM_PROVIDER=replay for placeholder summaries."
            )
        self._client = openai.AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
        self._model = model or os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL
        self._max_tokens = max_tokens

    async def complete(self, prompt: str) -> LlmResponse:
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
            # A short report needs little thinking; models that cannot be told so ignore it.
            # `usage.include` makes OpenRouter report what the call cost.
            extra_body={"reasoning": {"effort": "low"}, "usage": {"include": True}},
        )
        self._note_model(response.model)
        choice = response.choices[0]
        text = choice.message.content or ""
        usage = response.usage
        reported = (usage.model_dump() if usage else {}).get("cost")
        return LlmResponse(
            model=response.model,
            text=text,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            cost_usd=float(reported) if isinstance(reported, int | float) else None,
        )

    def _note_model(self, served: str | None) -> None:
        """Say which model answered. A router (``openrouter/auto``, ``openrouter/free``) picks per
        request and the choice is invisible unless looked for: a live run once found its summaries
        written by a reasoning model nobody chose. A pinned model that is served as another one
        (a fallback or an alias OpenRouter resolved) is worth a warning too."""
        if not served or served == self._model:
            return
        router = self._model.startswith("openrouter/")
        _LOG.log(
            logging.INFO if router else logging.WARNING,
            "OpenRouter served %r for a request to %r%s",
            served,
            self._model,
            ""
            if not router
            else f" (a router picks the model per request: pin one with {MODEL_ENV})",
        )
