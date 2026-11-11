"""Integration (CUT-207, ADR-0057): a team on a worker survives a controller restart and a worker
restart, and a project moves between workers. A real relay, connector and daemon; a worker is a raw
`WorkerLink` where the test needs to play one that dies, or a real `cuttlefish.worker` task."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from test_pairing_and_tiers import OWNER, Rig
from test_worker_join import FAST, _join, _until
from test_worker_placement import ROLES, _fake_kopicode, _get, _journal, _place, _project

from cuttlefish import runtime
from cuttlefish.episodic.events import (
    TaskFailed,
    TeamStopped,
)
from cuttlefish.fleet.placement import Placement
from cuttlefish.worker import run as worker_run
from cuttlefish.worker.link import WorkerLink


@pytest.fixture(autouse=True)
def _reset_runtime() -> Any:
    yield
    runtime.reset()


async def _open(identity: worker_run.Identity) -> WorkerLink:
    return await WorkerLink.open(
        identity.controller, identity.static, identity.secret, {"name": "laptop"}
    )


async def _work(link: WorkerLink, running: list[str] | None) -> dict[str, Any]:
    body: dict[str, Any] = {"wait": 0.2}
    if running is not None:
        body["running"] = running
    status, data = await link.request("POST", "/api/worker/work", body)
    assert status == 200
    return dict(data)


async def _started(
    rig: Rig, tmp_path: Path, name: str = "laptop"
) -> tuple[Any, dict[str, Any], str]:
    identity, _path = await _join(rig, tmp_path, name)
    rig.link.liveness.beat(identity.worker_id)
    project = await _project(rig, tmp_path)
    await _place(rig, project["id"], identity.worker_id)
    started = await rig.post(f"/api/projects/{project['id']}/start", **ROLES)
    return identity, project, started.json()["team_id"]


def _restart_controller(rig: Rig) -> None:
    """What a restart leaves: the stores on disk, and none of the daemon's memory."""
    lookup = rig.daemon.placement.lookup
    rig.daemon.placement = Placement()
    rig.daemon.placement.lookup = lookup


# -- a worker that is not running its team is handed it again -------------------------------------


async def test_a_team_the_worker_is_not_running_is_handed_over_again_to_resume(
    rig: Rig, tmp_path: Path
) -> None:
    identity, _proj, team_id = await _started(rig, tmp_path)
    link = await _open(identity)
    try:
        first = await _work(link, [])
        assert [o["team_id"] for o in first["orders"]] == [team_id]
        assert "resume" not in first["orders"][0]
        # It says it has the team: nothing more is handed over.
        assert (await _work(link, [team_id]))["orders"] == []
        # The process died and came back, running nothing: the same team, to resume.
        again = await _work(link, [])
        assert [(o["team_id"], o["resume"]) for o in again["orders"]] == [(team_id, True)]
        assert (await _work(link, [team_id]))["orders"] == []
        # An old worker that does not say what it runs is never handed anything twice.
        assert (await _work(link, None))["orders"] == []
    finally:
        await link.close()


async def test_a_stop_the_dead_worker_never_acted_on_goes_out_again(
    rig: Rig, tmp_path: Path
) -> None:
    identity, project, team_id = await _started(rig, tmp_path)
    link = await _open(identity)
    try:
        await _work(link, [])
        await rig.post(f"/api/projects/{project['id']}/stop")
        assert (await _work(link, [team_id]))["stop"] == [team_id]
        again = await _work(link, [])  # restarted before it could finish
        assert [o["team_id"] for o in again["orders"]] == [team_id]
        assert again["stop"] == [team_id]
    finally:
        await link.close()


# -- a controller restart -------------------------------------------------------------------------


async def test_a_team_on_a_worker_is_taken_back_after_a_controller_restart(
    rig: Rig, tmp_path: Path
) -> None:
    identity, project, team_id = await _started(rig, tmp_path)
    link = await _open(identity)
    first = {"seq": 1, "ts": "2026-11-12T10:00:00+00:00", "type": "TaskFailed",
             "data": {"error": "x", "role": "builder"}}  # fmt: skip
    second = {**first, "seq": 2}
    try:
        await _work(link, [])
        events = f"/api/worker/teams/{team_id}/events"
        assert (await link.request("POST", events, {"events": [first]}))[0] == 200

        _restart_controller(rig)
        assert rig.daemon.is_running(project["id"]) is False
        assert await rig.daemon.adopt_remote_teams() == 1
        assert rig.daemon.is_running(project["id"]) is True  # a placement, so no second team
        assert (await rig.post(f"/api/projects/{project['id']}/start", **ROLES)).status_code == 409

        # The worker kept running and goes on reporting; what the journal had is not doubled.
        status, body = await link.request("POST", events, {"events": [first, second]})
        assert status == 200 and body["last_seq"] == 2
        assert [type(p) for p in _journal(project["root"], team_id)] == [TaskFailed, TaskFailed]
        # Its poll says it still runs the team: nothing is handed over again.
        assert (await _work(link, [team_id]))["orders"] == []
        done = await link.request(
            "POST", f"/api/worker/teams/{team_id}/finish", {"status": "stopped"}
        )
        assert done[0] == 200
        assert rig.daemon.is_running(project["id"]) is False
        assert await rig.daemon.adopt_remote_teams() == 0  # it ended: nothing to take back
    finally:
        await link.close()


async def test_after_a_controller_restart_a_worker_that_lost_its_team_is_told_to_resume_it(
    rig: Rig, tmp_path: Path
) -> None:
    identity, _proj, team_id = await _started(rig, tmp_path)
    link = await _open(identity)
    try:
        await _work(link, [])
        _restart_controller(rig)
        await rig.daemon.adopt_remote_teams()
        orders = (await _work(link, []))["orders"]
        assert [(o["team_id"], o["resume"]) for o in orders] == [(team_id, True)]
        assert orders[0]["roles"] == [{"name": "builder", "text": "say hello", "allow": []}]
    finally:
        await link.close()


# -- a real worker with nothing to resume ---------------------------------------------------
# (A worker killed mid-round, with its own store to resume from, is exercised with real
# processes and `kill -9`: cancelling a task in this process is a clean stop, not a crash.)


async def test_a_worker_with_no_local_record_of_a_team_says_so_instead_of_starting_over(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch)
    identity, project, team_id = await _started(rig, tmp_path)
    link = await _open(identity)
    await _work(link, [])  # the order was taken, then the machine's state was lost
    await link.close()
    runner = asyncio.create_task(
        worker_run.run(identity, path=tmp_path / "worker-home" / ".cuttlefish" / "worker.json",
                       say=lambda _t: None, **FAST)
    )  # fmt: skip
    try:

        async def over() -> bool:
            return not rig.daemon.is_running(project["id"])

        await _until(over, 30)
        failed = [p for p in _journal(project["root"], team_id) if isinstance(p, TaskFailed)]
        assert failed and "no record of the team" in failed[0].error
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


# -- moving a project -----------------------------------------------------------------------------


async def _second_worker(rig: Rig, tmp_path: Path) -> worker_run.Identity:
    other, _path = await _join(rig, tmp_path / "second", "desktop")
    rig.link.liveness.beat(other.worker_id)
    return other


async def _move(rig: Rig, project_id: str, worker_id: str | None, **extra: Any) -> Any:
    return await rig.post(f"/api/projects/{project_id}/move", worker_id=worker_id, **extra)


async def test_a_move_stops_the_team_and_starts_it_again_on_the_other_worker(
    rig: Rig, tmp_path: Path
) -> None:
    first, project, team_id = await _started(rig, tmp_path / "first")
    second = await _second_worker(rig, tmp_path)
    link = await _open(first)
    await _work(link, [])

    async def old_worker_finishes() -> None:
        stops = (await _work(link, [team_id]))["stop"]
        assert stops == [team_id]
        await link.request(
            "POST",
            f"/api/worker/teams/{team_id}/finish",
            {"status": "stopped", "checkout": {"dirty": 0, "unpushed": 0}},
        )

    try:
        moving = asyncio.create_task(_move(rig, project["id"], second.worker_id))
        await _until(lambda: _stop_requested(rig, project["id"]), 10)
        await old_worker_finishes()
        moved = await asyncio.wait_for(moving, 15)
        assert moved.status_code == 200, moved.text
        body = moved.json()
        assert body["worker"]["id"] == second.worker_id and body["team_id"] != team_id
        assert body["running"] is True
        # The old team ended in the one journal; the new one is waiting on the new worker.
        assert [type(p) for p in _journal(project["root"], team_id)] == [TeamStopped]
        other = await _open(second)
        try:
            orders = (await _work(other, []))["orders"]
            assert [o["team_id"] for o in orders] == [body["team_id"]]
            assert orders[0]["roles"][0]["name"] == "builder"  # the same roles, as persisted
        finally:
            await other.close()
    finally:
        await link.close()


async def _stop_requested(rig: Rig, project_id: str) -> bool:
    return rig.daemon.is_stopping(project_id)


async def test_a_move_is_refused_before_anything_stops_when_it_cannot_happen(
    rig: Rig, tmp_path: Path
) -> None:
    _first, project, _team = await _started(rig, tmp_path / "first")
    offline, _p = await _join(rig, tmp_path / "off", "offline")  # paired, never heard from
    refused = await _move(rig, project["id"], offline.worker_id)
    assert refused.status_code == 409 and "offline" in refused.json()["detail"]
    assert (await _move(rig, project["id"], "nobody")).status_code == 400
    assert (await _move(rig, "nope", None)).status_code == 404
    same = await _move(rig, project["id"], (await _get(rig, project["id"]))["worker"]["id"])
    assert same.status_code == 409
    view = await _get(rig, project["id"])
    assert view["running"] is True and view["stopping"] is False  # the original is untouched


async def test_a_move_that_cannot_stop_the_team_in_time_leaves_it_on_its_worker(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _first, project, team_id = await _started(rig, tmp_path / "first")
    second = await _second_worker(rig, tmp_path)
    link = await _open(_first)
    await _work(link, [])  # taken, and then the worker says nothing at all
    try:
        with pytest.raises(Exception, match="still ending"):
            await rig.daemon.move(project["id"], second.worker_id, stop_wait=0.3)
        view = await _get(rig, project["id"])
        assert view["worker"]["id"] == _first.worker_id
        assert [type(p) for p in _journal(project["root"], team_id)] == []
    finally:
        await link.close()


async def test_work_the_old_worker_did_not_push_is_warned_about_and_never_silently_left(
    rig: Rig, tmp_path: Path
) -> None:
    first, project, team_id = await _started(rig, tmp_path / "first")
    second = await _second_worker(rig, tmp_path)
    link = await _open(first)
    try:
        await _work(link, [])
        await link.request(
            "POST",
            f"/api/worker/teams/{team_id}/finish",
            {"status": "completed", "checkout": {"dirty": 2, "unpushed": 1}},
        )
        view = await _get(rig, project["id"])
        assert "2 changed files not committed and 1 commit not pushed" in view["worker_handover"]
        for refused in (
            await _move(rig, project["id"], second.worker_id),
            await _place(rig, project["id"], None),
        ):
            assert refused.status_code == 409
            assert refused.json()["unpushed"] is True and "laptop holds" in refused.json()["detail"]
        assert (await _get(rig, project["id"]))["worker"]["id"] == first.worker_id
        # A person who has read it can go on, and the warning goes with the move.
        ok = await _move(rig, project["id"], second.worker_id, acknowledge_unpushed=True)
        assert ok.status_code == 200 and ok.json()["worker_handover"] is None
        assert ok.json()["worker"]["id"] == second.worker_id and ok.json()["team_id"] is None
    finally:
        await link.close()


async def test_a_clean_checkout_moves_without_a_question(rig: Rig, tmp_path: Path) -> None:
    first, project, team_id = await _started(rig, tmp_path / "first")
    second = await _second_worker(rig, tmp_path)
    link = await _open(first)
    try:
        await _work(link, [])
        await link.request(
            "POST",
            f"/api/worker/teams/{team_id}/finish",
            {"status": "completed", "checkout": {"dirty": 0, "unpushed": 0}},
        )
        assert (await _get(rig, project["id"]))["worker_handover"] is None
        assert (await _move(rig, project["id"], second.worker_id)).status_code == 200
    finally:
        await link.close()


async def test_a_machine_that_dies_with_a_team_is_a_warning_not_a_silent_move(
    rig: Rig, tmp_path: Path
) -> None:
    _first, project, _team = await _started(rig, tmp_path / "first")
    second = await _second_worker(rig, tmp_path)
    rig.link.liveness.forget(_first.worker_id)  # it went away with the team on it
    refused = await _move(rig, project["id"], second.worker_id)
    assert refused.status_code == 409 and refused.json()["unpushed"] is True
    assert "never reported" in refused.json()["detail"]
    ok = await _move(rig, project["id"], second.worker_id, acknowledge_unpushed=True)
    assert ok.status_code == 200 and ok.json()["team_id"] is not None  # carried on over there


# -- a removed worker -----------------------------------------------------------------------------


async def test_removing_a_worker_puts_a_card_in_needs_you_for_each_project_it_unplaced(
    rig: Rig, tmp_path: Path
) -> None:
    identity, project, _team = await _started(rig, tmp_path)
    removed = await rig.owner.delete(f"/api/workers/{identity.worker_id}", headers=OWNER)
    assert removed.status_code == 200
    pending = rig.daemon.requests.pending(project["id"])
    assert len(pending) == 1
    record = pending[0].record
    assert record.kind == "decision"
    assert "was on laptop, which was removed" in record.detail
    assert record.options == ["Keep it on this machine", "I will place it on another worker"]
    assert "laptop was removed" in (await _get(rig, project["id"]))["worker_handover"]


async def test_a_move_refused_after_the_stop_can_be_confirmed_and_still_carry_on(
    rig: Rig, tmp_path: Path
) -> None:
    first, project, team_id = await _started(rig, tmp_path / "first")
    second = await _second_worker(rig, tmp_path)
    link = await _open(first)
    await _work(link, [])
    try:
        moving = asyncio.create_task(_move(rig, project["id"], second.worker_id))
        await _until(lambda: _stop_requested(rig, project["id"]), 10)
        await _work(link, [team_id])
        await link.request(
            "POST",
            f"/api/worker/teams/{team_id}/finish",
            {"status": "stopped", "checkout": {"dirty": 3, "unpushed": 0}},
        )
        refused = await asyncio.wait_for(moving, 15)
        assert refused.status_code == 409 and refused.json()["unpushed"] is True
        assert "3 changed files" in refused.json()["detail"]
        assert "has stopped" in refused.json()["detail"]
        view = await _get(rig, project["id"])
        assert view["worker"]["id"] == first.worker_id and view["running"] is False
        ok = await _move(
            rig, project["id"], second.worker_id, acknowledge_unpushed=True, restart=True
        )
        assert ok.status_code == 200 and ok.json()["team_id"] is not None
        assert ok.json()["running"] is True and ok.json()["worker"]["id"] == second.worker_id
    finally:
        await link.close()
