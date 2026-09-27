"""Integration: `run_task`'s round-boundary approval gate (KAN-1711), against the
real kopicode binary -- no mock kopicode (docs/PLAN.md "Testing approach"). See
`test_workflow_steering.py`'s own module docstring for why a real, recorded
`DelegationOutcome` (not a missing-credential `DelegationError`) is needed, and
why `_INVALID_OPENROUTER_KEY` gets one with no real cost. Needs outbound network
access.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import satay
from satay.journal.store import SQLiteStore

from cuttlefish import runtime
from cuttlefish.episodic.events import ApprovalDecision, DelegationStarted
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
async def test_an_approval_finalizes_with_that_rounds_own_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    _configure_runtime(episodic_store)
    task_id = "approval-task-approved"
    root = tmp_path / "scratch"
    root.mkdir()

    async with satay.run_app(store=SQLiteStore.open(":memory:")) as store:
        handle = satay.start(
            run_task,
            {
                "task_id": task_id,
                "text": "add a .gitignore entry",
                "root": str(root),
                "require_approval": True,
            },
            run_id=task_id,
            store=store,
        )
        # A brief delay so the wait_for_event call is actually parked before the
        # decision arrives -- not load-bearing for correctness (an event delivered
        # before the wait still matches, satay's own guarantee), just realistic.
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

    episodic_store.close()


async def _wait_for_nth_delegation_started(
    episodic_store: EpisodicStore, task_id: str, n: int, *, timeout: float = 30.0
) -> None:
    """Poll `task_id`'s journal until its `n`th `DelegationStarted` has been
    written -- proof a new round genuinely began, not just a fixed sleep long
    enough to usually cover a real network round trip.

    Only useful *while something is actively driving the run* (a concurrent
    `asyncio.create_task(handle.result())`) -- `satay.start()` alone does not
    background-execute a workflow; nothing progresses at all until `.result()`
    (or an equivalent drive call) is actually awaited. Polling this before ever
    driving the run would just spin until `timeout` with zero events, always
    (verified live: it did, for the full 30s, before this fix).
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        events = list(episodic_store.read(task_id))
        if sum(1 for e in events if isinstance(e.payload, DelegationStarted)) >= n:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for DelegationStarted #{n}")


@pytest.mark.requires_kopicode
async def test_a_rejection_starts_a_second_round_with_the_comment_folded_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every round is gated, not just the first (Paperclip's own "each attempt to
    close needs sign-off" shape) -- so this test both rejects round 1 (proving
    the redirect) and approves round 2 (proving the gate applies there too, and
    letting the run actually finish).

    Drives `handle.result()` as its own concurrent task, polling/sending
    alongside it -- see `_wait_for_nth_delegation_started`'s own docstring for
    why (satay needs an active driver; nothing here backgrounds itself).
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    _configure_runtime(episodic_store)
    task_id = "approval-task-rejected"
    root = tmp_path / "scratch"
    root.mkdir()

    async with satay.run_app(store=SQLiteStore.open(":memory:")) as store:
        handle = satay.start(
            run_task,
            {
                "task_id": task_id,
                "text": "add a .gitignore entry",
                "root": str(root),
                "require_approval": True,
            },
            run_id=task_id,
            store=store,
        )
        drive = asyncio.create_task(handle.result())
        await _wait_for_nth_delegation_started(episodic_store, task_id, 1)
        await satay.send_event(
            ApprovalDecision(approved=False, comment="wrong file, use .dockerignore instead"),
            key=steering_key(task_id, None),
            store=store,
        )
        await _wait_for_nth_delegation_started(episodic_store, task_id, 2)
        await satay.send_event(
            ApprovalDecision(approved=True), key=steering_key(task_id, None), store=store
        )
        result = await drive

    assert result["status"] == "failed"

    events = list(episodic_store.read(task_id))
    kinds = [type(event.payload).__name__ for event in events]
    assert kinds.count("DelegationStarted") == 2
    assert kinds.count("ApprovalDecision") == 2
    assert kinds.count("TaskFailed") == 1  # finalized once, not once per round

    started = [event.payload for event in events if isinstance(event.payload, DelegationStarted)]
    assert "wrong file, use .dockerignore instead" in started[1].task_text
    assert "add a .gitignore entry" in started[1].task_text  # the original ask is still there

    episodic_store.close()


@pytest.mark.requires_kopicode
async def test_require_approval_never_times_out_on_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of a gate: unlike `--steerable`'s own short grace window, a
    round genuinely blocks until decided -- proven here by *actively driving* the
    run (see `_wait_for_nth_delegation_started`'s own docstring for why that's
    load-bearing, not decorative) well past what an ordinary steering grace would
    have been, and confirming the round is still waiting rather than having
    silently finalized. An earlier version of this test drove nothing and just
    slept, so its "not yet finalized" assertion was true vacuously (nothing had
    even started) rather than because the gate genuinely held -- caught live by
    `_wait_for_nth_delegation_started` timing out with zero events for the first,
    never-driven version of this whole file.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    _configure_runtime(episodic_store)
    task_id = "approval-task-no-timeout"
    root = tmp_path / "scratch"
    root.mkdir()

    async with satay.run_app(store=SQLiteStore.open(":memory:")) as store:
        handle = satay.start(
            run_task,
            {
                "task_id": task_id,
                "text": "add a .gitignore entry",
                "root": str(root),
                "require_approval": True,
            },
            run_id=task_id,
            store=store,
        )
        drive = asyncio.create_task(handle.result())
        await _wait_for_nth_delegation_started(episodic_store, task_id, 1)

        # Longer than DEFAULT_STEERING_GRACE_SECONDS (5s) -- if this workflow had
        # somehow reverted to a timeout-based wait, it would have already
        # finalized by now. `drive` is actively running throughout this sleep,
        # so a real timeout-based finalization would actually happen here.
        await asyncio.sleep(6.0)
        events_before_decision = list(episodic_store.read(task_id))
        assert not any(
            type(e.payload).__name__ in ("TaskCompleted", "TaskFailed")
            for e in events_before_decision
        )

        await satay.send_event(
            ApprovalDecision(approved=True), key=steering_key(task_id, None), store=store
        )
        result = await drive

    assert result["status"] == "failed"
    episodic_store.close()


@pytest.mark.requires_kopicode
async def test_require_approval_wins_over_steerable_in_the_same_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: `steerable=True` *and* `require_approval=True` together --
    exactly `cuttlefish.fleet.daemon`'s own configuration for every
    daemon-started team (`steerable` is unconditional there), which a real
    dashboard run surfaced as a genuine bug this test now guards against. Before
    the fix, awaiting `SteeringMessage` then `ApprovalDecision` in the same round
    collided on satay's own bare-ordinal wait identity (`event#N`, no type
    discriminator) -- the steering wait's own fired timeout got misread as the
    approval wait's, silently resolving `decision` to `None` and raising
    `AssertionError` inside the workflow (visible only as an unretrieved task
    exception in the fleet daemon, never surfaced to the operator). The fix
    makes `require_approval` replace the steering wait for a gated round rather
    than run both; this proves that combination now finalizes correctly instead
    of silently dying.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    _configure_runtime(episodic_store)
    task_id = "approval-task-steerable-and-require-approval"
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
                "require_approval": True,
            },
            run_id=task_id,
            store=store,
        )
        await asyncio.sleep(0.2)
        await satay.send_event(
            ApprovalDecision(approved=True), key=steering_key(task_id, None), store=store
        )
        result = await handle.result()

    assert result["status"] == "failed"

    events = list(episodic_store.read(task_id))
    kinds = [type(event.payload).__name__ for event in events]
    assert kinds.count("DelegationStarted") == 1
    assert kinds.count("ApprovalDecision") == 1
    assert "SteeringMessage" not in kinds  # never awaited when require_approval wins
    assert kinds.count("TaskFailed") == 1

    episodic_store.close()
