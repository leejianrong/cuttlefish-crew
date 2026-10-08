"""Integration: the real @satay.workflow, the real episodic journal, the real kopicode.

No mock kopicode (docs/PLAN.md "Testing approach"). The provider credential is
deliberately unset so the run reaches kopicode's own real "no provider configured"
outcome rather than a live model call — see
tests/integration/delegate/test_kopicode_real.py for why that's still a genuine,
unmocked round trip and not a stand-in for one. What this test actually exercises is
the mechanism build plan steps 2-4 assembled: the workflow drives the real
delegation task, which shells out to the real binary, and the failure it hits is
journaled as real episodic events — nothing here is faked except the one thing this
suite has no live credential for.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from satay.api.primitives import start
from satay.journal.store import SQLiteStore

from cuttlefish import runtime
from cuttlefish.episodic.events import (
    DelegationFailed,
    DelegationStarted,
    HandoverWritten,
    TaskFailed,
    TaskSubmitted,
)
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.llm.provider import LlmResponse
from cuttlefish.llm.replay import ReplayLlmProvider
from cuttlefish.sandbox.container import ContainerSandboxProvider
from cuttlefish.workflow import run_task


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


@pytest.mark.requires_kopicode
async def test_a_refused_delegation_is_a_journaled_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode",
        )
    )
    satay_store = SQLiteStore.open(":memory:")
    task_id = "test-task-1"
    root = tmp_path / "scratch"
    root.mkdir()

    handle = start(
        run_task,
        {"task_id": task_id, "text": "add a .gitignore entry", "root": str(root)},
        run_id=task_id,
        store=satay_store,
    )
    result = await handle.result()

    assert result["status"] == "failed"
    assert "error" in result

    events = list(episodic_store.read(task_id))
    kinds = [type(event.payload).__name__ for event in events]
    assert kinds == ["TaskSubmitted", "DelegationStarted", "DelegationFailed", "TaskFailed"]
    assert isinstance(events[0].payload, TaskSubmitted)
    assert events[0].payload.text == "add a .gitignore entry"
    assert isinstance(events[1].payload, DelegationStarted)
    assert isinstance(events[2].payload, DelegationFailed)
    assert isinstance(events[3].payload, TaskFailed)

    episodic_store.close()
    satay_store.close()


@pytest.mark.requires_kopicode
async def test_a_declared_allowlist_is_recorded_on_the_journaled_delegation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KAN-1011: an operator-declared, per-task policy replaces V1's fixed one."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode",
        )
    )
    satay_store = SQLiteStore.open(":memory:")
    task_id = "test-task-allow"
    root = tmp_path / "scratch"
    root.mkdir()
    declared_allow = [["go", "test"], ["npm", "test"]]

    handle = start(
        run_task,
        {
            "task_id": task_id,
            "text": "run the test suite",
            "root": str(root),
            "allow": declared_allow,
        },
        run_id=task_id,
        store=satay_store,
    )
    await handle.result()

    events = list(episodic_store.read(task_id))
    delegation_started = next(
        event.payload for event in events if isinstance(event.payload, DelegationStarted)
    )
    assert delegation_started.policy_allow == declared_allow

    episodic_store.close()
    satay_store.close()


@pytest.mark.requires_kopicode
@pytest.mark.requires_docker
async def test_a_sandboxed_delegation_is_recorded_and_still_reaches_the_real_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KAN-1010: with a sandbox configured, the delegation routes through it --
    proven mechanically (mount, exec, argv, cwd all correct) without a live model
    credential, the same "no provider configured" posture the direct-host tests
    already take. A real edit landing through the sandbox is a separate, live-
    credential-gated test (tests/integration/delegate/test_kopicode_sandbox_live.py).
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode",
            sandbox_provider=ContainerSandboxProvider(),
        )
    )
    satay_store = SQLiteStore.open(":memory:")
    task_id = "test-task-sandboxed"
    root = tmp_path / "scratch"
    root.mkdir()

    handle = start(
        run_task,
        {"task_id": task_id, "text": "add a .gitignore entry", "root": str(root)},
        run_id=task_id,
        store=satay_store,
    )
    result = await handle.result()

    assert result["status"] == "failed"

    events = list(episodic_store.read(task_id))
    delegation_started = next(
        event.payload for event in events if isinstance(event.payload, DelegationStarted)
    )
    assert delegation_started.sandbox == "container"
    delegation_failed = next(
        event.payload for event in events if isinstance(event.payload, DelegationFailed)
    )
    assert "OPENROUTER_API_KEY is not set" in delegation_failed.reason

    episodic_store.close()
    satay_store.close()


@pytest.mark.requires_kopicode
async def test_handover_fires_and_is_readable_from_the_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs/SLICES.md V1 integration test: the handover fires at the configured
    threshold and the resulting summary event is itself readable from the journal.
    """
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider(
                [
                    LlmResponse(model="replay", text="Done: handover after submission"),
                    LlmResponse(model="replay", text="Done: handover after delegation failure"),
                ]
            ),
            kopicode_binary="kopicode",
        )
    )
    satay_store = SQLiteStore.open(":memory:")
    task_id = "test-task-2"
    root = tmp_path / "scratch"
    root.mkdir()

    handle = start(
        run_task,
        {
            "task_id": task_id,
            "text": "add a .gitignore entry",
            "root": str(root),
            # Forces every non-empty window to trigger a handover, deterministically,
            # rather than growing a real episodic window past a realistic budget.
            "token_budget": 1,
        },
        run_id=task_id,
        store=satay_store,
    )
    await handle.result()

    events = list(episodic_store.read(task_id))
    kinds = [type(event.payload).__name__ for event in events]
    assert kinds == [
        "TaskSubmitted",
        "LlmCallCompleted",  # the summariser's own call, journaled for its cost
        "HandoverWritten",
        "DelegationStarted",
        "DelegationFailed",
        "LlmCallCompleted",
        "HandoverWritten",
        "TaskFailed",
    ]
    first_handover = events[2].payload
    assert isinstance(first_handover, HandoverWritten)
    assert first_handover.summary == "Done: handover after submission"
    assert first_handover.covers_seq_from == 1
    assert first_handover.covers_seq_to == 1

    # Covers both DelegationStarted (seq 3) and DelegationFailed (seq 4) -- the
    # whole window since the first handover.
    second_handover = events[6].payload
    assert isinstance(second_handover, HandoverWritten)
    assert second_handover.summary == "Done: handover after delegation failure"
    assert second_handover.covers_seq_from == 4
    assert second_handover.covers_seq_to == 5

    episodic_store.close()
    satay_store.close()
