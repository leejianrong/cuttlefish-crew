"""Integration (CUT-300, ADR-0057): a worker's report names the hand-over it belongs to, so a late
report from a replaced process cannot add to or end the team now running."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from test_pairing_and_tiers import Rig
from test_worker_join import _join
from test_worker_move import (
    ROLES,
    _journal,
    _open,
    _place,
    _project,
    _restart_controller,
    _work,
)

from cuttlefish.episodic.events import TaskFailed, TeamStopped

EVENT = {
    "seq": 1,
    "ts": "2026-11-12T10:00:00+00:00",
    "type": "TaskFailed",
    "data": {"error": "x", "role": "builder"},
}


async def _team(rig: Rig, tmp_path: Path) -> tuple[Any, dict[str, Any], str]:
    identity, _path = await _join(rig, tmp_path)
    rig.link.liveness.beat(identity.worker_id)
    project = await _project(rig, tmp_path)
    await _place(rig, project["id"], identity.worker_id)
    started = await rig.post(f"/api/projects/{project['id']}/start", **ROLES)
    return identity, project, started.json()["team_id"]


async def test_each_hand_over_has_its_own_token_and_only_the_latest_is_heard(
    rig: Rig, tmp_path: Path
) -> None:
    identity, project, team_id = await _team(rig, tmp_path)
    link = await _open(identity)
    events, finish = f"/api/worker/teams/{team_id}/events", f"/api/worker/teams/{team_id}/finish"
    try:
        first = (await _work(link, []))["orders"][0]["dispatch"]
        second = (await _work(link, []))["orders"][0]["dispatch"]  # the process died: handed again
        assert first and second and first != second
        # The replaced process is heard from late: neither its events nor its end are taken.
        for path, body in (
            (events, {"events": [EVENT], "dispatch": first}),
            (finish, {"status": "completed", "dispatch": first}),
            (finish, {"status": "completed", "dispatch": None}),
        ):
            assert (await link.request("POST", path, body))[0] == 404
        assert rig.daemon.is_running(project["id"]) is True
        assert _journal(project["root"], team_id) == []
        # The one that has the team now is.
        assert (await link.request("POST", events, {"events": [EVENT], "dispatch": second}))[
            0
        ] == 200
        assert (await link.request("POST", finish, {"status": "stopped", "dispatch": second}))[
            0
        ] == 200
        assert [type(p) for p in _journal(project["root"], team_id)] == [TaskFailed, TeamStopped]
        assert rig.daemon.is_running(project["id"]) is False
    finally:
        await link.close()


async def test_a_team_taken_back_after_a_restart_binds_to_the_first_token_it_hears(
    rig: Rig, tmp_path: Path
) -> None:
    identity, _proj, team_id = await _team(rig, tmp_path)
    link = await _open(identity)
    events = f"/api/worker/teams/{team_id}/events"
    try:
        held = (await _work(link, []))["orders"][0]["dispatch"]
        _restart_controller(rig)
        await rig.daemon.adopt_remote_teams()
        assert (await _work(link, [team_id]))["orders"] == []  # still running there
        assert (await link.request("POST", events, {"events": [], "dispatch": held}))[0] == 200
        assert (await link.request("POST", events, {"events": [], "dispatch": "someone-else"}))[
            0
        ] == 404
    finally:
        await link.close()
