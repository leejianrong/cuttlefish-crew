from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish import runtime
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.llm.provider import LlmResponse
from cuttlefish.llm.replay import ReplayLlmProvider
from cuttlefish.tasks.llm import call_llm


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


async def test_call_llm_returns_the_configured_providers_response(tmp_path: Path) -> None:
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store,
            llm_provider=ReplayLlmProvider(
                [LlmResponse(model="replay", text="a summary", input_tokens=10, output_tokens=4)]
            ),
            kopicode_binary="kopicode",
        )
    )

    result = await call_llm("summarise this")

    assert result == {
        "model": "replay",
        "text": "a summary",
        "input_tokens": 10,
        "output_tokens": 4,
        "cost_usd": None,
    }
    store.close()
