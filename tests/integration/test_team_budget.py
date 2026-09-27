"""Integration: `run_team`'s token/cost hard stop (KAN-1712/ADR-0017), against
the real kopicode binary. See `test_workflow_budget.py`'s own module docstring
for why `max_tokens=0` is a deterministic way to force the hard stop against
`_INVALID_OPENROUTER_KEY`'s always-zero-token failure, and
`test_team_approval.py`'s own module docstring for why every test here drives
`handle.result()` as its own concurrent task. Needs outbound network access.
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
    """See `test_team_approval.py`'s identical helper -- only meaningful while
    something is actively driving the run."""
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
async def test_a_zero_token_ceiling_blocks_every_role_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`max_tokens` applies team-wide but is checked per role (`team.TeamInput`'s
    own docstring) -- proven here by requiring each role's own `ApprovalDecision`
    before it finalizes, not one shared decision for the whole team."""
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    _configure_runtime(episodic_store)
    team_id = "budget-team-zero-ceiling"
    root = tmp_path / "scratch"
    root.mkdir()
    roles: list[RoleInput] = [
        {"name": "builder", "text": "add a .gitignore entry"},
        {"name": "reviewer", "text": "review the diff"},
    ]

    async with satay.run_app(store=SQLiteStore.open(":memory:")) as store:
        handle = satay.start(
            run_team,
            {"team_id": team_id, "root": str(root), "roles": roles, "max_tokens": 0},
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

    assert by_role["builder"].count("ApprovalDecision") == 1
    assert by_role["reviewer"].count("ApprovalDecision") == 1

    failed = next(
        e.payload
        for e in events
        if type(e.payload).__name__ == "DelegationFailed" and e.payload.role == "builder"
    )
    assert failed.tokens == 0  # confirms the ceiling really was crossed

    episodic_store.close()


@pytest.mark.requires_kopicode
async def test_no_ceiling_configured_never_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a plain team with no `max_tokens`/`max_cost_usd` set (today's
    exact default) finalizes on its own, exactly as it always has."""
    monkeypatch.setenv("OPENROUTER_API_KEY", _INVALID_OPENROUTER_KEY)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    _configure_runtime(episodic_store)
    team_id = "budget-team-no-ceiling"
    root = tmp_path / "scratch"
    root.mkdir()
    roles: list[RoleInput] = [{"name": "builder", "text": "add a .gitignore entry"}]

    async with satay.run_app(store=SQLiteStore.open(":memory:")) as store:
        handle = satay.start(
            run_team,
            {"team_id": team_id, "root": str(root), "roles": roles},
            run_id=team_id,
            store=store,
        )
        result = await handle.result()

    assert result["status"] == "failed"
    events = list(episodic_store.read(team_id))
    kinds = [type(event.payload).__name__ for event in events]
    assert "ApprovalDecision" not in kinds

    episodic_store.close()
