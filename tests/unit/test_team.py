"""Unit/integration: `run_team` -- real satay, real episodic journal, a deliberately
missing kopicode binary (the same no-mock discipline `test_delegate.py` and
`test_workflow.py` already hold, DelegationError being the real, fast failure mode
rather than something faked) -- ADR-0007.

Every test below runs the default `agent_backend` ("kopicode") with two or more
roles, so it already exercises `_dispatch_round`'s sequential-fallback branch
(Q44) on every run, not just the branch below that tests it directly -- these
assertions are agnostic to dispatch order, so they hold either way.
"""

from __future__ import annotations

from pathlib import Path

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
from cuttlefish.team import RoleInput, _needs_sequential_dispatch, run_team


def _roles(*names: str) -> list[RoleInput]:
    return [{"name": name, "text": f"do the {name} work"} for name in names]


def test_needs_sequential_dispatch_only_for_two_or_more_kopicode_backed_roles() -> None:
    """Q44: kopicode's own per-working-tree lock only collides once a *second*
    role tries the *same* root concurrently -- every role in a team already
    shares one root (`TeamInput.root` is singular), so this reduces to "kopicode,
    and more than one role active this round." Any other backend, or a lone
    active role, dispatches concurrently exactly as before."""
    both_kopicode = {"builder": "kopicode", "reviewer": "kopicode"}
    assert _needs_sequential_dispatch(["builder", "reviewer"], both_kopicode) is True
    assert _needs_sequential_dispatch(["builder"], both_kopicode) is False
    both_claude = {"builder": "claude-code", "reviewer": "claude-code"}
    assert _needs_sequential_dispatch(["builder", "reviewer"], both_claude) is False
    # KAN-1809: only roles actually on kopicode collide with each other.
    mixed = {"builder": "kopicode", "reviewer": "codex"}
    assert _needs_sequential_dispatch(["builder", "reviewer"], mixed) is False


async def test_every_roles_own_delegation_failure_is_journaled_under_its_own_role(
    tmp_path: Path,
) -> None:
    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode-binary-that-does-not-exist",
        )
    )
    satay_store = SQLiteStore.open(":memory:")
    team_id = "team-1"
    root = tmp_path / "scratch"
    root.mkdir()

    handle = start(
        run_team,
        {"team_id": team_id, "root": str(root), "roles": _roles("builder", "reviewer")},
        run_id=team_id,
        store=satay_store,
    )
    result = await handle.result()

    assert result["status"] == "failed"
    assert set(result["roles"]) == {"builder", "reviewer"}
    for role_result in result["roles"].values():
        assert role_result["status"] == "failed"
        assert "not found" in role_result["error"]

    events = list(episodic_store.read(team_id))
    by_role: dict[str | None, list[str]] = {}
    for event in events:
        by_role.setdefault(event.payload.role, []).append(type(event.payload).__name__)  # type: ignore[union-attr]

    for role in ("builder", "reviewer"):
        assert by_role[role] == [
            "TaskSubmitted",
            "DelegationStarted",
            "DelegationFailed",
            "TaskFailed",
        ]

    episodic_store.close()


async def test_a_role_name_appears_on_every_event_it_writes(tmp_path: Path) -> None:
    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode-binary-that-does-not-exist",
        )
    )
    satay_store = SQLiteStore.open(":memory:")
    team_id = "team-2"
    root = tmp_path / "scratch"
    root.mkdir()

    await start(
        run_team,
        {"team_id": team_id, "root": str(root), "roles": _roles("builder")},
        run_id=team_id,
        store=satay_store,
    ).result()

    events = list(episodic_store.read(team_id))
    submitted = next(e for e in events if isinstance(e.payload, TaskSubmitted))
    started = next(e for e in events if isinstance(e.payload, DelegationStarted))
    failed = next(e for e in events if isinstance(e.payload, DelegationFailed))
    task_failed = next(e for e in events if isinstance(e.payload, TaskFailed))
    assert submitted.payload.role == "builder"
    assert started.payload.role == "builder"
    assert failed.payload.role == "builder"
    assert task_failed.payload.role == "builder"

    episodic_store.close()


async def test_each_role_gets_its_own_handover_once_over_budget(tmp_path: Path) -> None:
    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            # Long role text reappears verbatim on DelegationStarted (the same
            # journal shape run_task already has), so more than one handover per
            # role can fire over this test's own small budget -- give it plenty
            # of replay responses rather than pinning an exact count.
            llm_provider=ReplayLlmProvider([LlmResponse(model="replay", text="a summary")] * 10),
            kopicode_binary="kopicode-binary-that-does-not-exist",
        )
    )
    satay_store = SQLiteStore.open(":memory:")
    team_id = "team-3"
    root = tmp_path / "scratch"
    root.mkdir()

    roles: list[RoleInput] = [
        {"name": "builder", "text": "x" * 4000},
        {"name": "reviewer", "text": "y" * 4000},
    ]
    await start(
        run_team,
        {"team_id": team_id, "root": str(root), "roles": roles, "token_budget": 50},
        run_id=team_id,
        store=satay_store,
    ).result()

    events = list(episodic_store.read(team_id))
    handovers = [e.payload for e in events if isinstance(e.payload, HandoverWritten)]
    roles_handed_over = {h.role for h in handovers}
    assert "builder" in roles_handed_over
    assert "reviewer" in roles_handed_over

    episodic_store.close()


async def test_a_role_can_name_its_own_backend_and_the_journal_records_it(
    tmp_path: Path,
) -> None:
    """KAN-1809: the default runtime backend is kopicode, but the `reviewer` role
    names codex -- each role's `DelegationStarted` records the backend it ran
    through (both fail fast here: the binaries deliberately don't exist)."""
    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode-binary-that-does-not-exist",
            codex_binary="codex-binary-that-does-not-exist",
        )
    )
    satay_store = SQLiteStore.open(":memory:")
    root = tmp_path / "scratch"
    root.mkdir()
    roles = _roles("builder", "reviewer")
    roles[1]["backend"] = "codex"

    await start(
        run_team,
        {"team_id": "team-b", "root": str(root), "roles": roles},
        run_id="team-b",
        store=satay_store,
    ).result()

    started = {
        e.payload.role: e.payload.backend
        for e in episodic_store.read("team-b")
        if isinstance(e.payload, DelegationStarted)
    }
    assert started == {"builder": "kopicode", "reviewer": "codex"}
    episodic_store.close()
