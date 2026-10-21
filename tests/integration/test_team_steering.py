"""Integration: `run_team`'s round-boundary steering (ADR-0008), against the real
kopicode binary. Verifies the specific correctness property ADR-0008 designed
for: each role's poll happens *sequentially*, after that round's `satay.gather`
resolves, so steering one role starts it on a second round without touching a
role nobody steered.

With no provider credential at all, kopicode fails before ever opening a session (a
raised `DelegationError`) -- steering deliberately never polls after that (ADR-0008's
own boundary), so this needs a real, recorded `DelegationOutcome` instead.
`_INVALID_OPENROUTER_KEY` (mirrors `test_crash_recovery.py`'s own pattern) is
syntactically valid but fake: kopicode opens a real session, sends one real request,
and gets a real (rejected, uncharged) 401 back. Needs outbound network access.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import satay
from satay.journal.store import SQLiteStore

from cuttlefish import runtime
from cuttlefish.episodic.events import DelegationStarted, SteeringMessage
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.steering import steering_key
from cuttlefish.team import RoleInput, run_team

#: See the module docstring -- real session, no real cost, no real credential.
_INVALID_OPENROUTER_KEY = "sk-or-v1-" + "0" * 64


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


@pytest.mark.requires_kopicode
async def test_steering_one_role_gives_it_a_second_round_without_touching_the_other(
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
    team_id = "steer-team-1"
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
                "steerable": True,
                "steering_grace": 0.3,
            },
            run_id=team_id,
            store=store,
        )
        await satay.send_event(
            SteeringMessage(text="focus on the .gitignore entry only", role="builder"),
            key=steering_key(team_id, "builder"),
            store=store,
        )
        result = await handle.result()

    assert result["status"] == "failed"
    assert set(result["roles"]) == {"builder", "reviewer"}

    events = list(episodic_store.read(team_id))
    by_role: dict[str | None, list[str]] = {}
    for event in events:
        by_role.setdefault(event.payload.role, []).append(type(event.payload).__name__)  # type: ignore[union-attr]

    assert by_role["builder"].count("DelegationStarted") == 2
    assert "SteeringMessage" in by_role["builder"]
    assert by_role["builder"].count("TaskFailed") == 1

    # The reviewer role never got steered -- exactly one round, same shape as a
    # non-steerable team (ADR-0007's own per-role event sequence, unchanged).
    assert by_role["reviewer"] == [
        "TaskSubmitted",
        "DelegationStarted",
        "DelegationFailed",
        "TaskFailed",
    ]

    builder_started = [
        event.payload
        for event in events
        if isinstance(event.payload, DelegationStarted) and event.payload.role == "builder"
    ]
    assert "focus on the .gitignore entry only" in builder_started[1].task_text

    episodic_store.close()
