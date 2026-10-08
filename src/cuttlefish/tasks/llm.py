"""cuttlefish's own reasoning calls, wrapped as a satay task (ADR-0001, S5).

Distinct from the kopicode delegation (``cuttlefish.tasks.delegate``): this is
cuttlefish answering its own questions — currently, the working-memory handover's
summarisation call — never the coding work itself.
"""

from __future__ import annotations

import satay

from cuttlefish import runtime


@satay.task()
async def call_llm(prompt: str) -> dict[str, str | int | float | None]:
    response = await runtime.current().llm_provider.complete(prompt)
    return {
        "model": response.model,
        "text": response.text,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": response.cost_usd,
    }
