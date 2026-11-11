"""Unit: placing a project on a worker (CUT-206, ADR-0057)."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cuttlefish.fleet.daemon import _remote_url
from cuttlefish.fleet.placement import Placement
from cuttlefish.fleet.tiers import Tier, required_tier
from cuttlefish.projects.store import ProjectNotFoundError, ProjectStore


def _bare(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Orders without their per-hand-over token, which is random."""
    return [{k: v for k, v in order.items() if k != "dispatch"} for order in orders]


def test_a_project_is_placed_on_a_worker_and_goes_back_when_it_is_removed(tmp_path: Path) -> None:
    store = ProjectStore.open(tmp_path / "p.db")
    a = store.register(name="a", root=str(tmp_path))
    b = store.register(name="b", root=str(tmp_path))
    c = store.register(name="c", root=str(tmp_path))
    assert a.worker_id is None  # runs here until a person places it
    store.update_worker(a.id, "w1")
    store.update_worker(b.id, "w1")
    store.update_worker(c.id, "w2")
    assert sorted(store.unplace_worker("w1")) == sorted([a.id, b.id])
    assert [store.get(p.id).worker_id for p in (a, b, c)] == [None, None, "w2"]
    assert store.update_worker(c.id, None).worker_id is None
    with pytest.raises(ProjectNotFoundError):
        store.update_worker("nope", "w1")


def test_a_store_made_before_placement_gains_the_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    ProjectStore.open(path).register(name="a", root=str(tmp_path))
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE projects DROP COLUMN worker_id")
    db.commit()
    db.close()
    assert [p.worker_id for p in ProjectStore.open(path).list()] == [None]


async def test_an_order_and_a_stop_are_each_handed_over_once() -> None:
    placement = Placement()
    placement.assign("p1", "w1", {"team_id": "t1"})
    placement.assign("p2", "w2", {"team_id": "t2"})
    first = await placement.claim("w1", 0)
    assert _bare(first["orders"]) == [{"team_id": "t1"}] and first["stop"] == []
    assert await placement.claim("w1", 0) == {"orders": [], "stop": []}  # not again
    team = placement.team("t1")
    assert team is not None
    team.stop_requested = True
    assert await placement.claim("w1", 0) == {"orders": [], "stop": ["t1"]}
    assert await placement.claim("w1", 0) == {"orders": [], "stop": []}
    assert (await placement.claim("w3", 0))["orders"] == []  # another worker gets nothing of w2's
    assert placement.end("t1") is team and placement.team_of_project("p1") is None


async def test_a_worker_waiting_for_work_gets_it_as_soon_as_it_exists() -> None:
    placement = Placement()
    waiting = asyncio.create_task(placement.claim("w1", 5))
    await asyncio.sleep(0.05)
    placement.assign("p1", "w1", {"team_id": "t1"})
    assert _bare((await asyncio.wait_for(waiting, 2))["orders"]) == [{"team_id": "t1"}]


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.mark.parametrize(
    ("origin", "sent"),
    [
        ("https://github.com/o/r.git", "https://github.com/o/r.git"),
        ("https://user:s3cret@github.com/o/r.git", "https://github.com/o/r.git"),
        ("https://x-access-token:abc@host:8443/o/r.git", "https://host:8443/o/r.git"),
        ("git@github.com:o/r.git", "git@github.com:o/r.git"),
    ],
)
def test_a_remote_is_sent_to_a_worker_without_a_credential_in_it(
    tmp_path: Path, origin: str, sent: str
) -> None:
    _git("init", "-q", cwd=tmp_path)
    _git("remote", "add", "origin", origin, cwd=tmp_path)
    assert _remote_url(str(tmp_path)) == sent


def test_a_folder_with_no_remote_sends_none(tmp_path: Path) -> None:
    assert _remote_url(str(tmp_path)) is None
    _git("init", "-q", cwd=tmp_path)
    assert _remote_url(str(tmp_path)) is None


def test_the_worker_routes_need_the_worker_tier_and_placing_needs_admin() -> None:
    for path in (
        "/api/worker/work",
        "/api/worker/teams/t/events",
        "/api/worker/teams/t/finish",
        "/api/worker/heartbeat",
    ):
        assert required_tier("POST", path) is Tier.WORKER
    assert required_tier("PATCH", "/api/projects/p/worker") is Tier.ADMIN


def test_a_store_made_before_moving_gains_its_columns_and_keeps_them(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    store = ProjectStore.open(path)
    project = store.register(name="a", root=str(tmp_path))
    store.set_worker_team(project.id, is_open=True, handover="w holds work not pushed")
    store.set_worker_team(project.id, is_open=False)  # what is left out is not changed
    saved = ProjectStore.open(path).get(project.id)
    assert (saved.worker_team_open, saved.worker_handover) == (False, "w holds work not pushed")
    store.set_worker_team(project.id, handover=None)
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE projects DROP COLUMN worker_team_open")
    db.execute("ALTER TABLE projects DROP COLUMN worker_handover")
    db.commit()
    db.close()
    again = ProjectStore.open(path).get(project.id)
    assert (again.worker_team_open, again.worker_handover) == (False, None)


async def test_a_team_the_worker_does_not_run_is_handed_over_again_marked_resume() -> None:
    placement = Placement()
    team = placement.assign("p1", "w1", {"team_id": "t1"})
    assert _bare((await placement.claim("w1", 0, set()))["orders"]) == [{"team_id": "t1"}]
    assert (await placement.claim("w1", 0, {"t1"}))["orders"] == []
    again = await placement.claim("w1", 0, set())
    assert _bare(again["orders"]) == [{"team_id": "t1", "resume": True}]
    team.stop_requested = True
    assert (await placement.claim("w1", 0, {"t1"}))["stop"] == ["t1"]
    assert (await placement.claim("w1", 0, set()))["stop"] == ["t1"]  # lost with the process


async def test_an_adopted_team_waits_to_be_told_whether_the_worker_still_runs_it() -> None:
    placement = Placement()
    team = placement.adopt("p1", "w1", {"team_id": "t1"}, last_seq=7)
    assert team.last_seq == 7
    assert (await placement.claim("w1", 0, {"t1"}))["orders"] == []  # it never stopped
    placement.adopt("p2", "w1", {"team_id": "t2"}, last_seq=0)
    adopted = (await placement.claim("w1", 0, {"t1"}))["orders"]
    assert _bare(adopted) == [{"team_id": "t2", "resume": True}]


async def test_every_hand_over_of_a_team_carries_a_new_token_the_team_then_holds() -> None:
    placement = Placement()
    team = placement.assign("p1", "w1", {"team_id": "t1"})
    first = (await placement.claim("w1", 0, set()))["orders"][0]["dispatch"]
    assert team.dispatch == first
    again = (await placement.claim("w1", 0, set()))["orders"][0]["dispatch"]
    assert again != first and team.dispatch == again
    adopted = placement.adopt("p2", "w1", {"team_id": "t2"}, last_seq=3)
    assert adopted.dispatch is None  # unknown until the worker reports, or is handed the team again
