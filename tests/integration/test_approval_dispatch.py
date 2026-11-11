"""Integration (CUT-300): a review decision is taken once, for the round the role is held after.

A decision is a message into a running team's inbox, matched to a waiting role by team and role.
Orca's rule is that a completion names its dispatch, so a stale retry cannot complete the wrong one;
here the dispatch is the role's round, counted from its journal."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cuttlefish.episodic.events import (
    DelegationCompleted,
    DelegationStarted,
    EventPayload,
    TaskSubmitted,
)
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.fleet.daemon import FleetDaemon, FleetError
from cuttlefish.projects.store import ProjectStore, RoleDefinition

TEAM = "team-1"


def _started(role: str = "builder") -> DelegationStarted:
    return DelegationStarted(task_text="t", root="/r", role=role)


def _done(role: str = "builder") -> DelegationCompleted:
    return DelegationCompleted(summary="s", role=role)


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, require_approval: bool
) -> tuple[FleetDaemon, str, EpisodicStore, list[dict[str, Any]]]:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    root = tmp_path / "alpha"
    root.mkdir()
    project = daemon.projects.register(
        name="alpha", root=str(root), roles=[RoleDefinition(name="builder", persona="p")]
    )
    daemon.projects.record_team_started(project.id, TEAM, require_approval=require_approval)
    store = EpisodicStore.open(root / ".cuttlefish" / "episodic.db")
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        daemon, "running", lambda _id: SimpleNamespace(team_id=TEAM, base_url="http://x", token="t")
    )
    monkeypatch.setattr(
        "cuttlefish.fleet.daemon.send_approval_decision", lambda **kw: sent.append(kw)
    )
    return daemon, project.id, store, sent


def _journal(store: EpisodicStore, *payloads: EventPayload) -> None:
    for payload in payloads:
        store.append(TEAM, payload)


async def test_a_decision_is_refused_while_the_role_is_still_working(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon, project_id, store, sent = _setup(tmp_path, monkeypatch, require_approval=True)
    _journal(store, TaskSubmitted(text="t", role="builder"), _started())
    with pytest.raises(FleetError, match="not waiting for a decision"):
        await daemon.approve(project_id, "builder", approved=True)
    assert sent == []  # nothing was queued to be taken by a later round's gate


async def test_a_decision_is_refused_for_a_team_with_no_review_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon, project_id, store, sent = _setup(tmp_path, monkeypatch, require_approval=False)
    _journal(store, TaskSubmitted(text="t", role="builder"), _started(), _done())
    with pytest.raises(FleetError, match="not waiting for a decision"):
        await daemon.approve(project_id, "builder", approved=True)
    assert sent == []


async def test_a_round_is_decided_once_and_the_next_round_can_be_decided_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon, project_id, store, sent = _setup(tmp_path, monkeypatch, require_approval=True)
    _journal(store, TaskSubmitted(text="t", role="builder"), _started(), _done())
    await daemon.approve(project_id, "builder", approved=False, comment="again")
    with pytest.raises(FleetError, match="already been decided for this round"):
        await daemon.approve(project_id, "builder", approved=True)  # a double click or a retry
    assert [item["approved"] for item in sent] == [False]
    _journal(store, _started(), _done())  # the redirected round ran and ended
    await daemon.approve(project_id, "builder", approved=True)
    assert [item["approved"] for item in sent] == [False, True]


async def test_another_roles_round_does_not_stand_in_for_this_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon, project_id, store, sent = _setup(tmp_path, monkeypatch, require_approval=True)
    _journal(store, _started("builder"), _done("builder"), _started("reviewer"))
    with pytest.raises(FleetError, match="reviewer is not waiting"):
        await daemon.approve(project_id, "reviewer", approved=True)
    await daemon.approve(project_id, "builder", approved=True)
    assert len(sent) == 1
