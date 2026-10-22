"""Integration: `run_task`'s round-boundary steering loop (ADR-0008), against the
real kopicode binary -- no mock kopicode (docs/PLAN.md "Testing approach"). With no
provider credential at all kopicode fails before ever opening a session (a raised
`DelegationError`, verified live) -- steering deliberately never polls after that
(ADR-0008's own boundary), so these tests need a real, recorded `DelegationOutcome`
instead. `_INVALID_OPENROUTER_KEY` (mirrors `test_crash_recovery.py`'s own pattern)
is syntactically valid but fake: kopicode opens a real session, sends one real
request, and gets a real (rejected, uncharged) 401 back -- a genuine `session_ended`,
no cost, no real credential ever used. Needs outbound network access.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import satay
from satay.journal.store import SQLiteStore

from cuttlefish import runtime
from cuttlefish.episodic.events import DelegationStarted, SteeringMessage
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.workflow import run_task

#: See the module docstring -- real session, no real cost, no real credential.
_INVALID_OPENROUTER_KEY = "sk-or-v1-" + "0" * 64


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


@pytest.mark.requires_kopicode
async def test_a_steering_message_starts_a_second_round_with_it_folded_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            kopicode_binary="kopicode",
        )
    )
    task_id = "steer-task-hit"
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
            },
            run_id=task_id,
            store=store,
        )
        await satay.send_event(
            SteeringMessage(text="actually add a .dockerignore instead"), key=task_id, store=store
        )
        result = await handle.result()

    assert result["status"] == "failed"

    events = list(episodic_store.read(task_id))
    kinds = [type(event.payload).__name__ for event in events]
    assert kinds.count("DelegationStarted") == 2
    assert kinds.count("SteeringMessage") == 1
    assert kinds.count("TaskFailed") == 1  # finalized once, not once per round

    started = [event.payload for event in events if isinstance(event.payload, DelegationStarted)]
    assert started[0].task_text == "add a .gitignore entry"
    assert "actually add a .dockerignore instead" in started[1].task_text
    assert "add a .gitignore entry" in started[1].task_text  # the original ask is still there

    episodic_store.close()


@pytest.mark.requires_kopicode
async def test_a_steerable_task_nobody_steers_finalizes_after_one_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            kopicode_binary="kopicode",
        )
    )
    task_id = "steer-task-miss"
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
                "steering_grace": 0.05,
            },
            run_id=task_id,
            store=store,
        )
        result = await handle.result()

    assert result["status"] == "failed"

    events = list(episodic_store.read(task_id))
    kinds = [type(event.payload).__name__ for event in events]
    assert kinds == ["TaskSubmitted", "DelegationStarted", "DelegationFailed", "TaskFailed"]

    episodic_store.close()
