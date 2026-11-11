"""Integration (CUT-206, ADR-0057): a project placed on a worker runs there. A real relay,
connector, daemon and a `cuttlefish.worker` running as a task, with the scripted fake `kopicode
serve` as the agent; the owner acts through the app's routes."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from test_pairing_and_tiers import OWNER, Rig
from test_worker_join import FAST, _join, _online, _until, _workers

from cuttlefish import runtime
from cuttlefish.episodic.events import (
    DelegationCompleted,
    StopRequested,
    TaskFailed,
    TeamStopped,
)
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.worker import run as worker_run
from cuttlefish.worker.link import WorkerLink

FAKE = Path(__file__).parents[2] / "unit" / "delegate" / "fake_kopicode_serve.py"
ROLES = {"roles": [{"name": "builder", "text": "say hello"}]}


@pytest.fixture(autouse=True)
def _reset_runtime() -> Any:
    yield
    runtime.reset()


def _fake_kopicode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = {
        "jsonrpc": "2.0",
        "id": "$start_id",
        "result": {"session": "$session", "record": "/r", "stop": "completed", "exit_code": 0},
    }
    ended = {
        "jsonrpc": "2.0",
        "method": "session.event",
        "params": {
            "session": "$session",
            "event": {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        },
    }
    scenario = [{"start": True}, {"emit": result}, {"close": [ended]}, {"eof": []}]
    (tmp_path / "scenario.json").write_text(json.dumps(scenario))
    monkeypatch.setenv("FAKE_KOPICODE_SCENARIO", str(tmp_path / "scenario.json"))
    monkeypatch.setenv("FAKE_KOPICODE_LOG", str(tmp_path / "sent.jsonl"))
    binary = tmp_path / "kopicode"
    binary.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_BIN", str(binary))
    monkeypatch.delenv("CUTTLEFISH_SECRETS_KEY", raising=False)


def _journal(root: str | Path, team_id: str) -> list[Any]:
    path = Path(root) / ".cuttlefish" / "episodic.db"
    if not path.exists():
        return []
    store = EpisodicStore.open(path)
    try:
        return [e.payload for e in store.read(team_id)]
    finally:
        store.close()


async def _project(rig: Rig, tmp_path: Path, name: str = "beta") -> dict[str, Any]:
    root = tmp_path / name
    root.mkdir()
    project = rig.daemon.projects.register(name=name, root=str(root), backend="kopicode")
    return {"id": project.id, "root": str(root)}


async def _place(rig: Rig, project_id: str, worker_id: str | None) -> Any:
    return await rig.owner.patch(
        f"/api/projects/{project_id}/worker", headers=OWNER, json={"worker_id": worker_id}
    )


async def _get(rig: Rig, project_id: str) -> dict[str, Any]:
    return dict((await rig.owner.get(f"/api/projects/{project_id}", headers=OWNER)).json())


async def test_a_project_placed_on_a_worker_runs_there_and_not_here(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch)
    identity, path = await _join(rig, tmp_path)
    runner = asyncio.create_task(worker_run.run(identity, path=path, say=lambda _t: None, **FAST))
    try:
        await _until(_online(rig))
        project = await _project(rig, tmp_path)
        placed = await _place(rig, project["id"], identity.worker_id)
        assert placed.status_code == 200
        assert placed.json()["worker"] == {
            "id": identity.worker_id,
            "name": "laptop",
            "online": True,
        }

        started = await rig.post(f"/api/projects/{project['id']}/start", **ROLES)
        assert started.status_code == 200, started.text
        team_id = started.json()["team_id"]
        assert (await _get(rig, project["id"]))["running"] is True

        async def finished() -> bool:
            return not rig.daemon.is_running(project["id"])

        await _until(finished, 30)
        # The controller's journal has what the worker's agent did, under the same team id.
        payloads = _journal(project["root"], team_id)
        assert any(isinstance(p, DelegationCompleted) for p in payloads)
        # The work happened in the worker's own checkout, with its own satay store and journal...
        checkout = path.parent / "worker-work" / project["id"]
        assert (checkout / ".git").exists() and (checkout / ".satay").exists()
        assert _journal(checkout, team_id), "the worker keeps its local journal"
        # ...and nothing ran here: the controller's folder has no satay store.
        assert not (Path(project["root"]) / ".satay").exists()
        view = await _get(rig, project["id"])
        assert view["running"] is False and view["status"]["builder"] in ("done", "blocked")
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


async def test_an_offline_worker_refuses_a_start_with_the_machine_named(
    rig: Rig, tmp_path: Path
) -> None:
    identity, path = await _join(rig, tmp_path)  # paired, but never heartbeated: offline
    project = await _project(rig, tmp_path)
    placed = await _place(rig, project["id"], identity.worker_id)
    assert placed.json()["worker"]["online"] is False  # the page can say so
    started = await rig.post(f"/api/projects/{project['id']}/start", **ROLES)
    assert started.status_code == 409
    assert "laptop" in started.json()["detail"] and "offline" in started.json()["detail"]
    assert rig.daemon.is_running(project["id"]) is False
    assert path.exists()


async def test_placement_is_validated_and_needs_a_stopped_project(rig: Rig, tmp_path: Path) -> None:
    identity, _path = await _join(rig, tmp_path)
    project = await _project(rig, tmp_path)
    assert (await _place(rig, project["id"], "no-such-worker")).status_code == 400
    assert (await _place(rig, "nope", identity.worker_id)).status_code == 404
    assert (
        await rig.owner.patch(
            f"/api/projects/{project['id']}/worker", headers=OWNER, json={"worker_id": 5}
        )
    ).status_code == 400
    assert (await _place(rig, project["id"], identity.worker_id)).status_code == 200
    assert (await _place(rig, project["id"], None)).json()["worker"] is None  # back here


async def test_the_builder_session_is_refused_on_a_worker_with_a_reason(
    rig: Rig, tmp_path: Path
) -> None:
    identity, _path = await _join(rig, tmp_path)
    project = await _project(rig, tmp_path)
    await _place(rig, project["id"], identity.worker_id)
    goal = await rig.post(f"/api/projects/{project['id']}/goal", text="build it")
    assert goal.status_code == 409
    assert "laptop" in goal.json()["detail"] and "not built for a worker" in goal.json()["detail"]


async def test_stopping_a_team_the_worker_has_not_taken_ends_it_here(
    rig: Rig, tmp_path: Path
) -> None:
    identity, _path = await _join(rig, tmp_path)
    rig.link.liveness.beat(identity.worker_id)  # online, but no process is polling
    project = await _project(rig, tmp_path)
    await _place(rig, project["id"], identity.worker_id)
    team_id = (await rig.post(f"/api/projects/{project['id']}/start", **ROLES)).json()["team_id"]
    assert (await rig.post(f"/api/projects/{project['id']}/stop")).status_code == 200
    assert not rig.daemon.is_running(project["id"])
    kinds = [type(p) for p in _journal(project["root"], team_id)]
    assert kinds == [StopRequested, TeamStopped]


async def test_a_stop_reaches_a_worker_that_took_the_team_and_a_dead_one_is_ended_here(
    rig: Rig, tmp_path: Path
) -> None:
    identity, _path = await _join(rig, tmp_path)
    rig.link.liveness.beat(identity.worker_id)
    project = await _project(rig, tmp_path)
    await _place(rig, project["id"], identity.worker_id)
    link = await WorkerLink.open(
        identity.controller, identity.static, identity.secret, {"name": "laptop"}
    )
    try:
        team_id = (await rig.post(f"/api/projects/{project['id']}/start", **ROLES)).json()[
            "team_id"
        ]
        status, body = await link.request("POST", "/api/worker/work", {"wait": 2})
        assert status == 200 and [o["team_id"] for o in body["orders"]] == [team_id]
        assert body["orders"][0]["roles"][0]["name"] == "builder"
        # Nothing secret or credential-shaped is in an order.
        assert set(body["orders"][0]) <= {
            "team_id", "project_id", "name", "secrets_scope", "backend", "roles",
            "require_approval", "repo", "max_tokens", "max_cost_usd",
        }  # fmt: skip
        await rig.post(f"/api/projects/{project['id']}/stop")
        assert (await _get(rig, project["id"]))["stopping"] is True
        _status, body = await link.request("POST", "/api/worker/work", {"wait": 2})
        assert body["stop"] == [team_id]
        # The worker says it is done; the journal records the stop and the project is free.
        done = await link.request(
            "POST", f"/api/worker/teams/{team_id}/finish", {"status": "stopped"}
        )
        assert done[0] == 200
        assert [type(p) for p in _journal(project["root"], team_id)] == [TeamStopped]
        assert not rig.daemon.is_running(project["id"])
    finally:
        await link.close()


async def test_a_worker_can_report_only_its_own_teams_and_repeats_change_nothing(
    rig: Rig, tmp_path: Path
) -> None:
    mine, _p = await _join(rig, tmp_path, "mine")
    rig.link.liveness.beat(mine.worker_id)
    project = await _project(rig, tmp_path)
    await _place(rig, project["id"], mine.worker_id)
    team_id = (await rig.post(f"/api/projects/{project['id']}/start", **ROLES)).json()["team_id"]
    link = await WorkerLink.open(mine.controller, mine.static, mine.secret, {"name": "mine"})
    event = {"seq": 1, "ts": "2026-11-12T10:00:00+00:00", "type": "TaskFailed",
             "data": {"error": "x", "role": "builder"}}  # fmt: skip
    try:
        for _ in range(2):  # a retry after a lost answer
            status, body = await link.request(
                "POST", f"/api/worker/teams/{team_id}/events", {"events": [event]}
            )
            assert status == 200 and body["last_seq"] == 1
        assert [type(p) for p in _journal(project["root"], team_id)] == [TaskFailed]
        for path, body in [
            (f"/api/worker/teams/{team_id}/events", {"events": "no"}),
            (f"/api/worker/teams/{team_id}/events", {"events": [{"seq": 2}]}),
            (f"/api/worker/teams/{team_id}/finish", {"status": "weird"}),
        ]:
            assert (await link.request("POST", path, body))[0] == 400
        assert (await link.request("POST", "/api/worker/teams/not-mine/events", {"events": []}))[
            0
        ] == 404
    finally:
        await link.close()
    # Another worker has no say over this team.
    rig.link.start_pairing("worker")
    other, _path = await _join(rig, tmp_path / "other", "other")
    stranger = await WorkerLink.open(other.controller, other.static, other.secret, {"name": "o"})
    try:
        assert (
            await stranger.request("POST", f"/api/worker/teams/{team_id}/events", {"events": []})
        )[0] == 404
        assert (
            await stranger.request(
                "POST", f"/api/worker/teams/{team_id}/finish", {"status": "stopped"}
            )
        )[0] == 404
    finally:
        await stranger.close()


async def test_removing_a_worker_ends_what_ran_on_it_and_unplaces_its_projects(
    rig: Rig, tmp_path: Path
) -> None:
    identity, _path = await _join(rig, tmp_path)
    rig.link.liveness.beat(identity.worker_id)
    project = await _project(rig, tmp_path)
    await _place(rig, project["id"], identity.worker_id)
    team_id = (await rig.post(f"/api/projects/{project['id']}/start", **ROLES)).json()["team_id"]
    assert (
        await rig.owner.delete(f"/api/workers/{identity.worker_id}", headers=OWNER)
    ).status_code == 200
    view = await _get(rig, project["id"])
    assert view["worker"] is None and view["running"] is False
    assert [type(p) for p in _journal(project["root"], team_id)] == [StopRequested, TeamStopped]
    assert await _workers(rig) == []


async def test_a_device_cannot_place_a_project_below_admin(rig: Rig, tmp_path: Path) -> None:
    identity, _path = await _join(rig, tmp_path)
    project = await _project(rig, tmp_path)
    device = await rig.pair()  # Approve
    browser = await rig.connect(device)
    try:
        status, _h, _b = await browser.request(
            "PATCH",
            f"/api/projects/{project['id']}/worker",
            json.dumps({"worker_id": identity.worker_id}).encode(),
        )
        assert status == 403
        for path in (
            "/api/worker/work",
            "/api/worker/teams/x/events",
            "/api/worker/teams/x/finish",
        ):
            assert (await browser.request("POST", path, b"{}"))[0] == 403
    finally:
        await browser.close()
