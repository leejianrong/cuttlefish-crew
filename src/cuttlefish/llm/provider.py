"""The ``LlmProvider`` seam for cuttlefish's own reasoning calls (S5, QUESTIONS.md Q11).

Distinct from the kopicode delegation: this is cuttlefish answering its own
questions (currently, the working-memory handover's summarisation call), never the
coding work itself, which is always delegated (ADR-0003).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LlmResponse:
    """One completed call, in the shape every provider implementation returns."""

    model: str
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: What the provider says the call cost, in US dollars, when it says (OpenRouter does);
    #: never estimated from a price table.
    cost_usd: float | None = None


class LlmProvider(Protocol):
    """A cuttlefish-facing LLM provider: one prompt in, one response out."""

    async def complete(self, prompt: str) -> LlmResponse: ...
