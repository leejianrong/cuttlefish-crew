"""Integration: the daemon's agent session in tmux (CUT-95) -- the HTTP routes, and a second
daemon reattaching to, or relaunching, what the first left behind."""

from __future__ import annotations

import json
import shutil
import stat
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import satay.control
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.projects.store import ProjectStore
from cuttlefish.sessions import SessionManager, Tmux

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "launches.log"
        self.log.touch()
        tool = self.bin / "claude"
        tool.write_text(f'#!/bin/sh\necho "claude $@" >> {self.log}\nexec sleep 300\n')
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        self.tmux = Tmux(tmp_path / "tmux.sock")
        self.store = ProjectStore.open(tmp_path / "projects.db")
        self.root = tmp_path / "alpha"
        self.root.mkdir()

    def daemon(self) -> FleetDaemon:
        """A fresh daemon over the same registry and tmux server, as after a restart."""
        manager = SessionManager(
            self.tmux,
            env_for=lambda _r, _b: {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.tmp)},
        )
        return FleetDaemon(self.store, sessions=manager)

    def launches(self) -> list[str]:
        time.sleep(0.6)
        return self.log.read_text().splitlines()


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path)
    yield r
    r.tmux._run("kill-server", check=False)
    r.store.close()


def _client(daemon: FleetDaemon) -> TestClient:
    app = create_app(daemon, security=satay.control.SecurityPolicy(token="t"))
    return TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"})


def test_the_routes_start_report_and_stop_a_session(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    assert client.get(f"/api/projects/{project.id}/agent-session").json()["state"] == "none"
    started = client.post(f"/api/projects/{project.id}/agent-session/start", json={}).json()
    assert started["state"] == "running" and started["last_action"] == "started"
    assert started["backend"] == "claude-code"
    stopped = client.post(f"/api/projects/{project.id}/agent-session/stop").json()
    assert stopped["state"] == "stopped"
    assert client.get("/api/projects/nope/agent-session").status_code == 404


def test_a_backend_with_no_interactive_tool_is_a_409_naming_it(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root))
    client = _client(rig.daemon())
    response = client.post(
        f"/api/projects/{project.id}/agent-session/start", json={"backend": "bogus"}
    )
    assert response.status_code == 409
    assert "bogus" in response.json()["detail"]


async def test_a_restarted_daemon_reattaches_to_the_agent_it_left_running(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    first = await rig.daemon().start_agent_session(project.id)
    assert rig.launches() == [f"claude --session-id {first.session_id}"]

    restored = await rig.daemon().reconcile_agent_sessions()  # a new daemon, same tmux server
    assert [(name, info.last_action) for name, info in restored] == [("alpha", "reattached")]  # type: ignore[union-attr]
    assert len(rig.launches()) == 1  # the agent was not touched


async def test_a_restarted_daemon_relaunches_with_resume_once_tmux_is_gone(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    first = await rig.daemon().start_agent_session(project.id)
    rig.tmux._run("kill-server")  # what a reboot does
    restored = await rig.daemon().reconcile_agent_sessions()
    info = restored[0][1]
    assert not isinstance(info, str) and info.last_action == "relaunched"
    assert rig.launches()[-1] == f"claude --resume {first.session_id}"


async def test_a_session_that_was_stopped_is_not_brought_back(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    daemon = rig.daemon()
    await daemon.start_agent_session(project.id)
    await daemon.stop_agent_session(project.id)
    assert await rig.daemon().reconcile_agent_sessions() == []
    assert (await rig.daemon().agent_session(project.id)).state == "stopped"


# -- the terminal websocket (CUT-96) ---------------------------------------------------------------


def _echoer(rig: Rig) -> None:
    """Make the fake `claude` an interactive thing a terminal can talk to."""
    tool = rig.bin / "claude"
    tool.write_text(
        f'#!/bin/sh\necho "claude $@" >> {rig.log}\necho READY\n'
        'while IFS= read -r line; do echo "pong: $line"; done\n'
    )


def _read_until(ws: Any, needle: bytes, tries: int = 60) -> bytes:
    seen = b""
    for _ in range(tries):
        message = ws.receive()
        if message.get("bytes"):
            seen += message["bytes"]
            if needle in seen:
                return seen
    raise AssertionError(f"never saw {needle!r}; got {seen[-200:]!r}")


def _connect(client: TestClient, path: str, **headers: str) -> Any:
    """A websocket as a loopback browser would open it (the test client's own host is not)."""
    return client.websocket_connect(path, headers={"host": "127.0.0.1", **headers})


def _auth(ws: Any, token: str = "t", **extra: Any) -> None:
    ws.send_text(json.dumps({"type": "auth", "token": token, **extra}))


def test_the_terminal_needs_the_token_first(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    with _connect(client, f"/api/projects/{project.id}/terminal") as ws:
        _auth(ws, token="wrong")
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert closed.value.code == 4401
    with _connect(client, f"/api/projects/{project.id}/terminal") as ws:
        ws.send_text("hello")
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert closed.value.code == 4400


def test_a_foreign_origin_is_refused_even_with_the_right_token(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    with _connect(
        client, f"/api/projects/{project.id}/terminal", origin="https://evil.example"
    ) as ws:
        _auth(ws)
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert closed.value.code == 4401


def test_a_project_with_no_running_session_says_so_and_is_not_started(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    daemon = rig.daemon()
    client = _client(daemon)
    with _connect(client, f"/api/projects/{project.id}/terminal") as ws:
        _auth(ws)
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert closed.value.code == 4404 and "start it from the Overview" in closed.value.reason
    assert client.get(f"/api/projects/{project.id}/agent-session").json()["state"] == "none"
    with _connect(client, "/api/projects/nope/terminal") as ws:
        _auth(ws)
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert closed.value.code == 4404


def test_typing_in_the_terminal_reaches_the_agent_and_its_output_comes_back(rig: Rig) -> None:
    _echoer(rig)
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    client.post(f"/api/projects/{project.id}/agent-session/start", json={})
    with _connect(client, f"/api/projects/{project.id}/terminal") as ws:
        _auth(ws, size={"cols": 100, "rows": 30})
        assert json.loads(ws.receive_text()) == {"type": "ready"}
        _read_until(ws, b"READY")
        ws.send_text(json.dumps({"type": "resize", "cols": 80, "rows": 24}))
        ws.send_bytes(b"over the wire\r")
        _read_until(ws, b"pong: over the wire")
    # closing the view left the agent running
    assert client.get(f"/api/projects/{project.id}/agent-session").json()["state"] == "running"
