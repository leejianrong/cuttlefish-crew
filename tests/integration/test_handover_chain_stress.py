"""Integration: does the handover chain hold across many chained rounds (ADR-0010/KAN-1704,
ADR-0042)?

Before ADR-0010, `HandoverWritten` was journaled but never read back by anything (a write-only
checkpoint), and the mechanism that *did* thread context across steered rounds
(`round_summaries` in `compose_steered_text`) had no token budget at all -- it grew forever, one raw
round at a time. `maybe_handover` also only ever fired before the round loop started and after it
ended, never once *during* a long steered run.

This test drives a real, many-round steered `run_task` (`token_budget=0` forces a handover after
literally every round, so a 10-round run chains 11 handovers deep) and asserts what a bug in either
mechanism would break: every later round's own composed prompt carries the latest handover, and its
size stays roughly constant instead of growing with the round count.

Since CUT-140 a handover is the agent's own document or a digest read from the journal; cuttlefish
no longer asks a model of its own to write one, so there is no summariser to script. Whether a real
agent's handoff keeps a load-bearing fact is a live-model question this deterministic test cannot
answer.

Real kopicode, no mock (docs/PLAN.md "Testing approach") -- an invalid-but-syntactically-valid
credential gets a real, fast, no-cost `DelegationFailed` every round, so ten real subprocess
invocations stay cheap and deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import satay
from satay.journal.store import SQLiteStore

from cuttlefish import runtime
from cuttlefish.episodic.events import DelegationStarted, HandoverWritten, SteeringMessage
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.llm.replay import ReplayLlmProvider
from cuttlefish.workflow import run_task

#: See the module docstring -- real session, no real cost, no real credential.
_INVALID_OPENROUTER_KEY = "sk-or-v1-" + "0" * 64

#: "10+ handovers deep" per the ticket's own wording.
_ROUNDS = 10

_MARKER = "CONSTRAINT: pin to Python 3.11 only"


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


@pytest.mark.requires_kopicode
async def test_ten_chained_handovers_reach_every_round_and_size_stays_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode",
        )
    )
    task_id = "handover-stress"
    root = tmp_path / "scratch"
    root.mkdir()

    async with satay.run_app(store=SQLiteStore.open(":memory:")) as store:
        handle = satay.start(
            run_task,
            {
                "task_id": task_id,
                "text": "add a .gitignore entry",
                "root": str(root),
                "steerable": True,
                "steering_grace": 0.3,
                "token_budget": 0,
            },
            run_id=task_id,
            store=store,
        )
        # One fewer send than _ROUNDS -- the last round is left to time out and
        # finalize, the same "nobody steers the final round" shape
        # test_workflow_steering.py's own miss test already covers. FIFO delivery
        # on one key (satay-runtime ADR-0021) means all of these can be queued
        # up front; each round's own wait consumes exactly one.
        for i in range(1, _ROUNDS):
            await satay.send_event(
                SteeringMessage(text=f"{_MARKER} (steer {i})"), key=task_id, store=store
            )
        result = await handle.result()

    assert result["status"] == "failed"  # the invalid credential, every round

    events = list(episodic_store.read(task_id))
    kinds = [type(event.payload).__name__ for event in events]
    assert kinds.count("DelegationStarted") == _ROUNDS
    assert kinds.count("HandoverWritten") == _ROUNDS + 1

    handovers = [e.payload for e in events if isinstance(e.payload, HandoverWritten)]
    assert all("A digest read from the journal" in h.summary for h in handovers)

    started = [e.payload for e in events if isinstance(e.payload, DelegationStarted)]
    assert isinstance(started[0], DelegationStarted)
    # The first round runs before any round has finished, so no handover has been
    # read back into it yet -- it's still the plain original text.
    assert "A digest read from the journal" not in started[0].task_text
    # Every later round's own composed prompt carries the latest handover, and with it what the
    # person said: the property that was silently broken before ADR-0010 (write-only).
    for round_input in started[1:]:
        assert "A digest read from the journal" in round_input.task_text
        assert _MARKER in round_input.task_text

    # Bounded growth: round_summaries resets every time a handover fires
    # (token_budget=0 fires one every round), so each round's own composed text
    # stays roughly the same size instead of growing linearly with round count --
    # the concrete, unbounded-growth defect ADR-0010 named in compose_steered_text.
    lengths = [len(round_input.task_text) for round_input in started]
    assert max(lengths[1:]) - min(lengths[1:]) < 1500  # capped, not linear in the rounds

    episodic_store.close()
