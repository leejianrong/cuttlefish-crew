"""Integration: `run_team`'s round-boundary approval gate (KAN-1711), against the
real kopicode binary. See `test_workflow_approval.py`'s own module docstring for
why a real, recorded `DelegationOutcome` is needed (`_INVALID_OPENROUTER_KEY`) and
why every test here drives `handle.result()` as its own concurrent task rather
than polling before ever awaiting it (satay needs an active driver to make any
progress at all -- verified live, not assumed). Needs outbound network access.
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
from cuttlefish.team import RoleInput, run_team

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


async def _wait_for_nth_delegation_started(
    episodic_store: EpisodicStore, team_id: str, role: str, n: int, *, timeout: float = 30.0
) -> None:
    """See `test_workflow_approval.py`'s identical helper -- only meaningful
    while something is actively driving the run."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        events = list(episodic_store.read(team_id))
        count = sum(
            1 for e in events if isinstance(e.payload, DelegationStarted) and e.payload.role == role
        )
        if count >= n:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"timed out waiting for {role}'s DelegationStarted #{n}")


@pytest.mark.requires_kopicode
async def test_approving_one_role_finalizes_it_without_touching_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    _configure_runtime(episodic_store)
    team_id = "approval-team-approved"
    root = tmp_path / "scratch"
    root.mkdir()
    roles: list[RoleInput] = [
        {"name": "builder", "text": "add a .gitignore entry"},
        {"name": "reviewer", "text": "review the diff"},
    ]

    async with satay.run_app(store=SQLiteStore.open(":memory:")) as store:
        handle = satay.start(
            run_team,
            {
                "team_id": team_id,
                "root": str(root),
                "roles": roles,
                "require_approval": True,
            },
            run_id=team_id,
            store=store,
        )
        drive = asyncio.create_task(handle.result())
        await _wait_for_nth_delegation_started(episodic_store, team_id, "builder", 1)
        await _wait_for_nth_delegation_started(episodic_store, team_id, "reviewer", 1)
        await satay.send_event(
            ApprovalDecision(approved=True, role="builder"),
            key=steering_key(team_id, "builder"),
            store=store,
        )
        await satay.send_event(
            ApprovalDecision(approved=True, role="reviewer"),
            key=steering_key(team_id, "reviewer"),
            store=store,
        )
        result = await drive

    assert result["status"] == "failed"
    assert set(result["roles"]) == {"builder", "reviewer"}

    events = list(episodic_store.read(team_id))
    by_role: dict[str | None, list[str]] = {}
    for event in events:
        by_role.setdefault(event.payload.role, []).append(type(event.payload).__name__)  # type: ignore[union-attr]

    assert by_role["builder"].count("DelegationStarted") == 1
    assert by_role["reviewer"].count("DelegationStarted") == 1
    assert by_role["builder"].count("ApprovalDecision") == 1
    assert by_role["reviewer"].count("ApprovalDecision") == 1

    episodic_store.close()


@pytest.mark.requires_kopicode
async def test_rejecting_one_role_gives_it_a_second_round_without_touching_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    _configure_runtime(episodic_store)
    team_id = "approval-team-rejected"
    root = tmp_path / "scratch"
    root.mkdir()
    roles: list[RoleInput] = [
        {"name": "builder", "text": "add a .gitignore entry"},
        {"name": "reviewer", "text": "review the diff"},
    ]

    async with satay.run_app(store=SQLiteStore.open(":memory:")) as store:
        handle = satay.start(
            run_team,
            {
                "team_id": team_id,
                "root": str(root),
                "roles": roles,
                "require_approval": True,
            },
            run_id=team_id,
            store=store,
        )
        drive = asyncio.create_task(handle.result())
        await _wait_for_nth_delegation_started(episodic_store, team_id, "builder", 1)
        await _wait_for_nth_delegation_started(episodic_store, team_id, "reviewer", 1)

        # Reject the builder only; approve the reviewer straight away.
        await satay.send_event(
            ApprovalDecision(approved=False, comment="focus on .gitignore only", role="builder"),
            key=steering_key(team_id, "builder"),
            store=store,
        )
        await satay.send_event(
            ApprovalDecision(approved=True, role="reviewer"),
            key=steering_key(team_id, "reviewer"),
            store=store,
        )

        await _wait_for_nth_delegation_started(episodic_store, team_id, "builder", 2)
        await satay.send_event(
            ApprovalDecision(approved=True, role="builder"),
            key=steering_key(team_id, "builder"),
            store=store,
        )
        result = await drive

    assert result["status"] == "failed"
    assert set(result["roles"]) == {"builder", "reviewer"}

    events = list(episodic_store.read(team_id))
    by_role: dict[str | None, list[str]] = {}
    for event in events:
        by_role.setdefault(event.payload.role, []).append(type(event.payload).__name__)  # type: ignore[union-attr]

    assert by_role["builder"].count("DelegationStarted") == 2
    assert by_role["builder"].count("ApprovalDecision") == 2
    assert by_role["builder"].count("TaskFailed") == 1  # finalized once, not per round

    # The reviewer was approved on its first round -- exactly one round, same
    # shape as a non-approval-gated team (ADR-0007's own per-role sequence).
    assert by_role["reviewer"].count("DelegationStarted") == 1
    assert by_role["reviewer"].count("ApprovalDecision") == 1

    builder_started = [
        event.payload
        for event in events
        if isinstance(event.payload, DelegationStarted) and event.payload.role == "builder"
    ]
    assert "focus on .gitignore only" in builder_started[1].task_text

    episodic_store.close()


@pytest.mark.requires_kopicode
async def test_require_approval_wins_over_steerable_in_the_same_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: `steerable=True` *and* `require_approval=True` together --
    exactly `cuttlefish.fleet.daemon`'s own configuration for every
    daemon-started team. See `test_workflow_approval.py`'s identical test for
    the full story: a real dashboard run surfaced a genuine satay-level
    wait-identity collision (bare `event#N` ordinals, no type discriminator)
    when both a `SteeringMessage` and an `ApprovalDecision` wait were awaited in
    the same round; the fix makes `require_approval` replace the steering wait
    rather than compose with it.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    _configure_runtime(episodic_store)
    team_id = "approval-team-steerable-and-require-approval"
    root = tmp_path / "scratch"
    root.mkdir()
    roles: list[RoleInput] = [{"name": "builder", "text": "add a .gitignore entry"}]

    async with satay.run_app(store=SQLiteStore.open(":memory:")) as store:
        handle = satay.start(
            run_team,
            {
                "team_id": team_id,
                "root": str(root),
                "roles": roles,
                "steerable": True,
                "require_approval": True,
            },
            run_id=team_id,
            store=store,
        )
        await asyncio.sleep(0.2)
        await satay.send_event(
            ApprovalDecision(approved=True, role="builder"),
            key=steering_key(team_id, "builder"),
            store=store,
        )
        result = await handle.result()

    assert result["status"] == "failed"

    events = list(episodic_store.read(team_id))
    kinds = [type(event.payload).__name__ for event in events]
    assert kinds.count("DelegationStarted") == 1
    assert kinds.count("ApprovalDecision") == 1
    assert "SteeringMessage" not in kinds
    assert kinds.count("TaskFailed") == 1

    episodic_store.close()
