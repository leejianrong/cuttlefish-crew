"""Integration (CUT-216, ADR-0057): a project's docker compose stack runs on its worker and its
web port is reached through the channel. A real relay, connector, daemon and worker; the worker's
`docker` is a script that records what it was asked and reports one published port, which a real
local HTTP server answers. The real `docker compose` was exercised by hand against a real worker,
not here."""

from __future__ import annotations

import asyncio
import json
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from test_pairing_and_tiers import OWNER, Rig
from test_worker_join import FAST, _join, _online, _until
from test_worker_placement import _place, _project

from cuttlefish.worker import run as worker_run
from cuttlefish.worker.execute import checkout


class _Page(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"<h1>hello from the stack</h1>" if self.path == "/" else b"second page"
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        pass


@pytest.fixture
def web() -> Any:
    server = HTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def _fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, port: int) -> Path:
    log = tmp_path / "docker.log"
    ps = {
        "Service": "web",
        "Publishers": [
            {"URL": "127.0.0.1", "TargetPort": 8000, "PublishedPort": port, "Protocol": "tcp"},
            {"URL": "", "TargetPort": 9000, "PublishedPort": 0, "Protocol": "tcp"},
        ],
    }
    (tmp_path / "ps.json").write_text(json.dumps(ps))
    binary = tmp_path / "bin" / "docker"
    binary.parent.mkdir()
    binary.write_text(
        f'#!/bin/sh\necho "$@" >> {log}\n'
        f'case "$*" in *" ps "*) cat {tmp_path / "ps.json"} ;; esac\n'
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{binary.parent}:/usr/bin:/bin")
    return log


async def _stack(rig: Rig, project_id: str) -> Any:
    return (await rig.owner.get(f"/api/projects/{project_id}/stack", headers=OWNER)).json()["stack"]


def _state(rig: Rig, project_id: str, state: str) -> Any:
    async def check() -> bool:
        got = await _stack(rig, project_id)
        return bool(got and got["state"] == state)

    return check


async def test_a_stack_comes_up_on_the_worker_is_reached_and_goes_down_with_the_project(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, web: int
) -> None:
    log = _fake_docker(tmp_path, monkeypatch, web)
    identity, path = await _join(rig, tmp_path)
    runner = asyncio.create_task(worker_run.run(identity, path=path, say=lambda _t: None, **FAST))
    try:
        await _until(_online(rig))
        project = await _project(rig, tmp_path)
        pid = project["id"]
        assert (await _place(rig, pid, identity.worker_id)).status_code == 200
        assert await _stack(rig, pid) is None
        root = checkout(path.parent / "worker-work", pid, None)
        (root / "compose.yaml").write_text("services: {web: {image: x}}\n")

        started = await rig.post(f"/api/projects/{pid}/stack/start")
        assert started.status_code == 200, started.text
        assert started.json()["stack"]["state"] == "starting"
        await _until(_state(rig, pid, "up"), 15)
        shown = await _stack(rig, pid)
        assert shown["worker"] == "laptop"
        assert shown["ports"] == [{"service": "web", "target": 8000, "published": web}]

        page = await rig.owner.get(
            f"/api/projects/{pid}/stack/page", headers=OWNER, params={"port": web, "path": "/"}
        )
        assert page.status_code == 200 and page.text == "<h1>hello from the stack</h1>"
        # It is served so that its scripts cannot reach the dashboard's origin or its token.
        assert page.headers["content-security-policy"].startswith("sandbox")
        other = await rig.owner.get(
            f"/api/projects/{pid}/stack/page", headers=OWNER, params={"port": web, "path": "/b"}
        )
        assert other.text == "second page"
        # Only a port the stack published, and only a plain path.
        for params in ({"port": web + 1, "path": "/"}, {"port": web, "path": "/../etc/passwd"}):
            refused = await rig.owner.get(
                f"/api/projects/{pid}/stack/page", headers=OWNER, params=params
            )
            assert refused.status_code == 409, params

        # A project with a stack cannot be moved or removed out from under it.
        assert (await _place(rig, pid, None)).status_code == 409
        assert (await rig.owner.delete(f"/api/projects/{pid}", headers=OWNER)).status_code == 409

        stopped = await rig.post(f"/api/projects/{pid}/stop")
        assert stopped.status_code == 200, stopped.text
        await _until(_state(rig, pid, "down"), 15)
        assert (await _stack(rig, pid))["ports"] == []
        # (the worker's own tool check also runs `docker --version`; it is not a stack call)
        calls = [c for c in log.read_text().splitlines() if c.startswith("compose -p")]
        assert any(" up -d" in c for c in calls) and any(" down " in c for c in calls)
        assert all(f"-p cuttlefish-{pid}" in c for c in calls)
        gone = await rig.owner.get(
            f"/api/projects/{pid}/stack/page", headers=OWNER, params={"port": web, "path": "/"}
        )
        assert gone.status_code == 409
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


async def test_a_stack_needs_a_worker_and_says_when_the_project_has_no_compose_file(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, web: int
) -> None:
    _fake_docker(tmp_path, monkeypatch, web)
    project = await _project(rig, tmp_path)
    here = await rig.post(f"/api/projects/{project['id']}/stack/start")
    assert here.status_code == 409 and "place the project on one" in here.json()["detail"]
    assert (await rig.post("/api/projects/nope/stack/start")).status_code == 404

    identity, path = await _join(rig, tmp_path)
    runner = asyncio.create_task(worker_run.run(identity, path=path, say=lambda _t: None, **FAST))
    try:
        await _until(_online(rig))
        assert (await _place(rig, project["id"], identity.worker_id)).status_code == 200
        started = await rig.post(f"/api/projects/{project['id']}/stack/start")
        assert started.status_code == 200
        await _until(_state(rig, project["id"], "failed"), 15)
        assert "no compose file" in (await _stack(rig, project["id"]))["error"]
        # A failed start can be stopped (it may have left something running).
        assert (await rig.post(f"/api/projects/{project['id']}/stack/stop")).status_code == 200
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


async def test_removing_the_worker_forgets_its_stack(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, web: int
) -> None:
    _fake_docker(tmp_path, monkeypatch, web)
    identity, path = await _join(rig, tmp_path)
    runner = asyncio.create_task(worker_run.run(identity, path=path, say=lambda _t: None, **FAST))
    try:
        await _until(_online(rig))
        project = await _project(rig, tmp_path)
        pid = project["id"]
        await _place(rig, pid, identity.worker_id)
        rig.daemon.stacks.request(pid, identity.worker_id, "laptop", None, "up")
        removed = await rig.owner.delete(f"/api/workers/{identity.worker_id}", headers=OWNER)
        assert removed.status_code == 200
        assert await _stack(rig, pid) is None
        assert (await rig.owner.delete(f"/api/projects/{pid}", headers=OWNER)).status_code == 204
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
