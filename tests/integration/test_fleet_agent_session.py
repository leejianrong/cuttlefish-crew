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
from cuttlefish.sessions.tmux import session_name, shell_session_name

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
    assert [x.split(" --append")[0] for x in rig.launches()] == [
        f"claude --session-id {first.session_id}"
    ]

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
    assert rig.launches()[-1].startswith(f"claude --resume {first.session_id} ")


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


def _wait_until_detached(rig: Rig, project_id: str, timeout: float = 10.0) -> None:
    # A project id names its agent's session; a session name (the shell's) is used as it is.
    name = project_id if project_id.startswith("cf-") else session_name(project_id)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        listed = rig.tmux._run("list-clients", "-t", f"={name}", check=False).stdout
        if not listed.strip():
            return
        time.sleep(0.05)
    raise AssertionError("the terminal's tmux client never detached")


def _connect(client: TestClient, path: str, **headers: str) -> Any:
    """A websocket as a loopback browser would open it (the test client's own host is not)."""
    return client.websocket_connect(path, headers={"host": "127.0.0.1", **headers})


def _auth(ws: Any, token: str = "t", **extra: Any) -> None:
    ws.send_text(json.dumps({"type": "auth", "token": token, **extra}))


def test_giving_a_goal_starts_the_builder_with_it_and_records_it(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    assert client.get(f"/api/projects/{project.id}/goal").json() == {"goal": None}
    given = client.post(f"/api/projects/{project.id}/goal", json={"text": "  Add a summary  "})
    assert given.status_code == 200
    body = given.json()
    assert body["goal"]["text"] == "Add a summary"
    assert body["session"]["state"] == "running" and body["session"]["last_action"] == "started"
    assert rig.launches()[0].endswith("Add a summary")
    assert client.get(f"/api/projects/{project.id}/goal").json()["goal"]["text"] == "Add a summary"


def test_a_goal_for_a_running_builder_is_refused_and_not_recorded(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    client.post(f"/api/projects/{project.id}/agent-session/start", json={})
    refused = client.post(f"/api/projects/{project.id}/goal", json={"text": "Another"})
    assert refused.status_code == 409 and "already running" in refused.json()["detail"]
    assert client.get(f"/api/projects/{project.id}/goal").json() == {"goal": None}


def test_a_goal_needs_text_and_a_known_project(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    assert client.post(f"/api/projects/{project.id}/goal", json={}).status_code == 400
    assert client.post(f"/api/projects/{project.id}/goal", json={"text": "   "}).status_code == 409
    assert client.post("/api/projects/nope/goal", json={"text": "x"}).status_code == 404


def _transcript_for(home: Path, root: Path, session_id: str, lines: list[dict[str, Any]]) -> Path:
    from cuttlefish.sessions.transcript import project_dir_name

    folder = home / ".claude" / "projects" / project_dir_name(str(root.resolve()))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


def test_the_chat_is_read_from_the_claude_code_transcript(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(rig.tmp))
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    assert client.get(f"/api/projects/{project.id}/chat").json()["supported"] is False  # no session
    session = client.post(f"/api/projects/{project.id}/agent-session/start", json={}).json()
    waiting = client.get(f"/api/projects/{project.id}/chat").json()
    assert waiting["supported"] and waiting["waiting"] and waiting["items"] == []
    path = _transcript_for(
        rig.tmp,
        rig.root,
        session["session_id"],
        [
            {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "Hello"}},
            {
                "type": "assistant",
                "uuid": "a1",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
            },
        ],
    )
    first = client.get(f"/api/projects/{project.id}/chat").json()
    assert [i["text"] for i in first["items"]] == ["Hello", "Hi"]
    assert first["path"] == str(path) and first["session"] == "running"
    with path.open("a") as handle:
        handle.write(
            json.dumps(
                {"type": "user", "uuid": "u2", "message": {"role": "user", "content": "More"}}
            )
            + "\n"
        )
    more = client.get(f"/api/projects/{project.id}/chat", params={"after": first["cursor"]}).json()
    assert [i["text"] for i in more["items"]] == ["More"]
    assert client.get(f"/api/projects/{project.id}/chat", params={"after": -1}).status_code == 400
    assert client.get("/api/projects/nope/chat").status_code == 404


def test_the_chat_of_another_tool_says_it_is_not_there_yet(rig: Rig) -> None:
    (rig.bin / "codex").write_text("#!/bin/sh\nexec sleep 300\n")
    (rig.bin / "codex").chmod(0o755)
    project = rig.store.register(name="alpha", root=str(rig.root), backend="codex")
    client = _client(rig.daemon())
    client.post(f"/api/projects/{project.id}/agent-session/start", json={})
    body = client.get(f"/api/projects/{project.id}/chat").json()
    assert body["supported"] is False and body["backend"] == "codex" and body["items"] == []


# -- typing into the session from the chat box (CUT-173) ---------------------------------------


def _raw_reader(rig: Rig, *, banner: str = "") -> Path:
    """Make the fake `claude` a terminal program that asks for bracketed paste, reads raw bytes and
    logs each one it gets. Returns the log. `banner` is printed first (a dialog, say)."""
    log = rig.tmp / "keys.log"
    tool = rig.bin / "claude"
    tool.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, termios, tty\n"
        f"sys.stdout.write({banner!r} + '\\x1b[?2004h'); sys.stdout.flush()\n"
        "tty.setraw(0)\n"
        f"out = open({str(log)!r}, 'ab', buffering=0)\n"
        "while True:\n"
        "    data = os.read(0, 4096)\n"
        "    if not data: break\n"
        "    out.write(data)\n"
    )
    tool.chmod(0o755)
    return log


def _bytes_seen(log: Path, needle: bytes, seconds: float = 6.0) -> bytes:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if log.exists() and needle in log.read_bytes():
            return log.read_bytes()
        time.sleep(0.05)
    raise AssertionError(f"never saw {needle!r}; got {log.read_bytes() if log.exists() else b''!r}")


def _started(rig: Rig, backend: str = "claude-code") -> tuple[TestClient, str]:
    project = rig.store.register(name="alpha", root=str(rig.root), backend=backend)
    client = _client(rig.daemon())
    client.post(f"/api/projects/{project.id}/agent-session/start", json={})
    time.sleep(0.8)  # the tool has to be up and have asked for bracketed paste
    return client, project.id


def test_a_chat_message_is_pasted_as_one_bracketed_block_and_then_sent(rig: Rig) -> None:
    log = _raw_reader(rig)
    client, pid = _started(rig)
    sent = client.post(f"/api/projects/{pid}/chat", json={"text": "line one\nline two"})
    assert sent.status_code == 200 and sent.json()["sent"] in ("delivered", "queued")
    seen = _bytes_seen(log, b"\r")
    # One paste with both lines inside the markers (so the newline is not an Enter), then Enter.
    assert seen == b"\x1b[200~line one\nline two\x1b[201~\r"


def test_escape_sequences_in_a_message_cannot_break_out_of_the_paste(rig: Rig) -> None:
    log = _raw_reader(rig)
    client, pid = _started(rig)
    client.post(f"/api/projects/{pid}/chat", json={"text": "a\x1b[201~y\x1b[200~b"})
    seen = _bytes_seen(log, b"\r")
    assert seen == b"\x1b[200~a[201~y[200~b\x1b[201~\r"


def test_nothing_is_typed_while_a_dialog_is_on_the_screen(rig: Rig) -> None:
    banner = "Do you want to proceed?\\r\\n\u276f 1. Yes\\r\\n  2. No\\r\\n"
    log = _raw_reader(rig, banner=banner)
    client, pid = _started(rig)
    refused = client.post(f"/api/projects/{pid}/chat", json={"text": "hello"})
    assert refused.status_code == 409 and "waiting on a permission" in refused.json()["detail"]
    time.sleep(0.5)
    assert not log.exists() or log.read_bytes() == b""


def test_a_message_needs_a_running_claude_code_session(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    path = f"/api/projects/{project.id}/chat"
    assert client.post(path, json={"text": "hi"}).status_code == 409  # not running
    assert client.post(path, json={}).status_code == 400
    assert client.post("/api/projects/nope/chat", json={"text": "hi"}).status_code == 404
    started = _started(rig)
    assert started[0].post(path, json={"text": "   "}).status_code == 409  # nothing to say


def test_a_codex_builder_is_told_the_chat_input_is_not_wired_in_yet(rig: Rig) -> None:
    (rig.bin / "codex").write_text("#!/bin/sh\nexec sleep 300\n")
    (rig.bin / "codex").chmod(0o755)
    client, pid = _started(rig, backend="codex")
    refused = client.post(f"/api/projects/{pid}/chat", json={"text": "hi"})
    assert (
        refused.status_code == 409 and "only wired in for Claude Code" in refused.json()["detail"]
    )


def test_the_chat_reports_what_the_session_is_doing(rig: Rig) -> None:
    _raw_reader(rig)
    client, pid = _started(rig)
    body = client.get(f"/api/projects/{pid}/chat").json()
    assert body["activity"]["state"] == "unknown"  # no hook has fired yet


def test_a_goal_with_autopilot_tells_the_builder_to_plan_first_and_waits_for_approval(
    rig: Rig,
) -> None:
    log = _raw_reader(rig)
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    # One portal for the whole test: the journal's sqlite connection belongs to the loop thread that
    # opened it, as it does in the daemon (a client with no `with` starts a thread per request).
    with _client(rig.daemon()) as client:
        _autopilot_flow(rig, client, project.id, log)


def _autopilot_flow(rig: Rig, client: TestClient, project_id: str, log: Path) -> None:
    path = f"/api/projects/{project_id}"
    assert client.get(f"{path}/autopilot").json()["phase"] == "off"
    given = client.post(f"{path}/goal", json={"text": "Build it", "autopilot": {"hours": 2}})
    assert given.status_code == 200
    time.sleep(0.8)
    state = client.get(f"{path}/autopilot").json()
    assert state["phase"] == "planning" and state["limits"]["hours"] == 2
    assert state["goal"] == "Build it" and state["plan"] is None
    refused = client.post(f"{path}/autopilot/approve")
    assert refused.status_code == 409 and "not written a checklist" in refused.json()["detail"]
    plan = rig.root / ".cuttlefish" / "artifacts" / "plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("- [x] one\n- [ ] two\n")
    approved = client.post(f"{path}/autopilot/approve")
    assert approved.status_code == 200 and approved.json()["phase"] == "running"
    assert approved.json()["plan"] == {
        "steps": [{"text": "one", "done": True}, {"text": "two", "done": False}],
        "done": 1,
        "total": 2,
    }
    assert b"The plan is approved" in _bytes_seen(log, b"approved")
    assert client.post(f"{path}/autopilot/pause").json()["phase"] == "paused"
    assert client.post(f"{path}/autopilot/stop").json()["phase"] == "stopped"
    assert client.post(f"{path}/autopilot/dance").status_code == 409


def test_autopilot_limits_are_checked_and_the_plan_instruction_reaches_claude(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    path = f"/api/projects/{project.id}/goal"
    for bad in ("yes", {"hours": -1}, {"tokens": "lots"}, {"nudges": 0}, [1]):
        assert client.post(path, json={"text": "x", "autopilot": bad}).status_code == 400
    assert client.post(path, json={"text": "Build it", "autopilot": True}).status_code == 200
    assert "Plan first" in rig.launches()[0]


def test_a_goal_without_autopilot_does_not_ask_for_a_plan(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    client.post(f"/api/projects/{project.id}/goal", json={"text": "Just do it"})
    assert "Plan first" not in rig.launches()[0]
    assert client.get(f"/api/projects/{project.id}/autopilot").json()["phase"] == "off"


def test_the_overview_gathers_the_goal_the_timeline_and_a_digest(rig: Rig) -> None:
    import subprocess

    who = {"NAME": "t", "EMAIL": "t@e.co"}
    env = {
        f"GIT_{role}_{key}": value for role in ("AUTHOR", "COMMITTER") for key, value in who.items()
    }
    env |= {"PATH": "/usr/bin:/bin", "HOME": str(rig.tmp)}
    for args in (["init", "-q", "-b", "feat/x"], ["add", "."], ["commit", "-q", "-m", "first"]):
        (rig.root / "a.txt").write_text("a")
        subprocess.run(
            ["git", "-C", str(rig.root), *args], check=True, env=env, capture_output=True
        )
    art = rig.root / ".cuttlefish" / "artifacts"
    art.mkdir(parents=True)
    (art / "chart.png").write_bytes(b"\x89PNG")
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    path = f"/api/projects/{project.id}/overview"
    body = client.get(path, params={"since": "2000-01-01T00:00:00Z"}).json()
    assert body["branch"] == "feat/x" and body["autopilot"]["phase"] == "off"
    texts = [e["text"] for e in body["timeline"]]
    assert "Commit: first" in texts and "Produced an image: chart.png" in texts
    assert "1 commit on feat/x." in body["digest"]
    quiet = client.get(path, params={"since": "2999-01-01T00:00:00Z"}).json()
    assert quiet["digest"] == [] and len(quiet["timeline"]) == len(texts)  # the timeline is not cut
    assert client.get(path).status_code == 200  # a day back, when the page remembers nothing
    for bad in ("yesterday", "2026-10-22", "2026-10-22T09:00:00+02:00"):
        assert client.get(path, params={"since": bad}).status_code == 400
    assert client.get("/api/projects/nope/overview").status_code == 404


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


def test_a_shell_terminal_needs_no_agent_session_and_is_the_same_route(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    with _connect(client, f"/api/projects/{project.id}/terminal") as ws:
        _auth(ws, kind="shell", size={"cols": 100, "rows": 30})
        assert json.loads(ws.receive_text()) == {"type": "ready"}
        # Type once the shell has drawn its prompt: bytes sent while it starts can be dropped.
        _read_until(ws, b"$ ")
        ws.send_bytes(b"echo shell-$((6*7))\r")
        _read_until(ws, b"shell-42")
        ws.close(1000)
        _wait_until_detached(rig, shell_session_name(project.id))
    # No agent was started for it, and closing the view left the shell running.
    assert client.get(f"/api/projects/{project.id}/agent-session").json()["state"] == "none"
    assert rig.tmux.has_session(shell_session_name(project.id))


def test_a_terminal_kind_that_is_not_known_is_refused(rig: Rig) -> None:
    project = rig.store.register(name="alpha", root=str(rig.root), backend="claude-code")
    client = _client(rig.daemon())
    with _connect(client, f"/api/projects/{project.id}/terminal") as ws:
        _auth(ws, kind="root")
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert closed.value.code == 4404 and "no terminal called" in closed.value.reason


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
        # Leave the view and wait for the daemon to finish detaching. The test client cancels the
        # app the moment its `with` block ends, without waiting for the handler's cleanup, so a
        # handler still waiting for tmux's client to go made the exit fail now and then (CUT-125).
        ws.close(1000)
        _wait_until_detached(rig, project.id)
    # closing the view left the agent running
    assert client.get(f"/api/projects/{project.id}/agent-session").json()["state"] == "running"
