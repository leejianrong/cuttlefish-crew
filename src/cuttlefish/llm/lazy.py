"""A provider that defers building the real one until its first call (KAN-1807).

cuttlefish's own LLM is used only for handover summaries, which many short runs never
reach; building it eagerly made a missing ``OPENROUTER_API_KEY`` block a run that would
never have called it (e.g. a subscription-only Codex or Claude Code user).
"""

from __future__ import annotations

from collections.abc import Callable

from cuttlefish.llm.provider import LlmProvider, LlmResponse


class LazyLlmProvider:
    """Calls `factory` once, on the first ``complete``; a failure there is not cached."""

    def __init__(self, factory: Callable[[], LlmProvider]) -> None:
        self._factory = factory
        self._provider: LlmProvider | None = None

    async def complete(self, prompt: str) -> LlmResponse:
        if self._provider is None:
            self._provider = self._factory()
        return await self._provider.complete(prompt)
