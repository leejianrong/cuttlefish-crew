"""Integration: `run_task`'s token/cost hard stop (KAN-1712/ADR-0017), against the
real kopicode binary -- no mock kopicode (docs/PLAN.md "Testing approach"). See
`test_workflow_approval.py`'s own module docstring for why a real, recorded
`DelegationOutcome` (not a missing-credential `DelegationError`) is needed, and
why `_INVALID_OPENROUTER_KEY` gets one with no real cost -- which also means no
`provider_response` line ever lands (kopicode fails before a real response comes
back), so a real run against it always reports `tokens=0`. `max_tokens=0` is
therefore a deterministic way to force the hard stop on round one without
depending on exactly how many tokens a real session actually burns. Needs
outbound network access.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import satay
from satay.journal.store import SQLiteStore

from cuttlefish import runtime
from cuttlefish.episodic.events import ApprovalDecision
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.llm.replay import ReplayLlmProvider
from cuttlefish.steering import steering_key
from cuttlefish.workflow import run_task

_INVALID_OPENROUTER_KEY = "sk-or-v1-" + "0" * 64


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


def _configure_runtime(episodic_store: EpisodicStore) -> None:
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode",
        )
    )


@pytest.mark.requires_kopicode
async def test_a_zero_token_ceiling_forces_the_approval_wait_after_round_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    _configure_runtime(episodic_store)
    task_id = "budget-task-zero-ceiling"
    root = tmp_path / "scratch"
    root.mkdir()

    async with satay.run_app(store=SQLiteStore.open(":memory:")) as store:
        handle = satay.start(
            run_task,
            {
                "task_id": task_id,
                "text": "add a .gitignore entry",
                "root": str(root),
                "max_tokens": 0,
            },
            run_id=task_id,
            store=store,
        )
        # A brief delay so the wait_for_event call is actually parked before the
        # decision arrives -- not load-bearing for correctness, just realistic.
        await asyncio.sleep(0.2)
        await satay.send_event(
            ApprovalDecision(approved=True), key=steering_key(task_id, None), store=store
        )
        result = await handle.result()

    assert result["status"] == "failed"  # the real DelegationOutcome kopicode returned

    events = list(episodic_store.read(task_id))
    kinds = [type(event.payload).__name__ for event in events]
    assert kinds.count("DelegationStarted") == 1  # approved -- no second round
    assert kinds.count("ApprovalDecision") == 1
    assert kinds.count("TaskFailed") == 1

    failed = next(e.payload for e in events if type(e.payload).__name__ == "DelegationFailed")
    assert failed.tokens == 0  # confirms the ceiling really was crossed, not vacuously true

    episodic_store.close()


@pytest.mark.requires_kopicode
async def test_no_ceiling_configured_never_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a plain run with neither `max_tokens` nor `max_cost_usd` set
    (today's exact default) must finalize on its own, exactly as it always has --
    `budget.exceeded` returning `False` for `(None, None)` is the load-bearing
    guarantee this asserts end-to-end, not just at the unit level."""
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    _configure_runtime(episodic_store)
    task_id = "budget-task-no-ceiling"
    root = tmp_path / "scratch"
    root.mkdir()

    async with satay.run_app(store=SQLiteStore.open(":memory:")) as store:
        handle = satay.start(
            run_task,
            {"task_id": task_id, "text": "add a .gitignore entry", "root": str(root)},
            run_id=task_id,
            store=store,
        )
        result = await handle.result()

    assert result["status"] == "failed"
    events = list(episodic_store.read(task_id))
    kinds = [type(event.payload).__name__ for event in events]
    assert "ApprovalDecision" not in kinds
    assert kinds.count("TaskFailed") == 1

    episodic_store.close()
