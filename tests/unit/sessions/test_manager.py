"""Unit: a project's agent in tmux (CUT-95) against fake tools on a scratch tmux socket."""

from __future__ import annotations

import json
import shutil
import stat
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from cuttlefish.artifacts import AGENT_NOTE
from cuttlefish.sessions import SessionError, SessionManager, Tmux
from cuttlefish.sessions.manager import launch_argv, wanted

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "project"
        self.root.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "launches.log"
        self.log.touch()
        for tool in ("claude", "codex", "kopicode"):
            path = self.bin / tool
            path.write_text(f'#!/bin/sh\necho "{tool} $@" >> {self.log}\nexec sleep 300\n')
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        self.tmux = Tmux(tmp_path / "tmux.sock")
        self.manager = SessionManager(
            self.tmux,
            env_for=lambda _root, _backend: {
                "PATH": f"{self.bin}:/usr/bin:/bin",
                "HOME": str(tmp_path),
            },
            kopicode_binary=str(self.bin / "kopicode"),
        )

    def launches(self) -> list[str]:
        for _ in range(50):
            lines = self.log.read_text().splitlines()
            if lines:
                time.sleep(0.1)
                return self.log.read_text().splitlines()
            time.sleep(0.1)
        return []


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    r = Rig(tmp_path)
    yield r
    r.tmux._run("kill-server", check=False)


def test_the_first_ensure_starts_the_tool_with_a_session_id(rig: Rig) -> None:
    info = rig.manager.ensure("p1", str(rig.root), "claude-code")
    assert info.state == "running" and info.last_action == "started"
    assert info.session_id
    assert [x.split(" --append")[0] for x in rig.launches()] == [
        f"claude --session-id {info.session_id}"
    ]
    assert wanted(str(rig.root))


def test_ensure_again_reattaches_and_launches_nothing(rig: Rig) -> None:
    first = rig.manager.ensure("p1", str(rig.root), "claude-code")
    rig.launches()
    again = rig.manager.ensure("p1", str(rig.root), "claude-code")
    assert again.last_action == "reattached" and again.session_id == first.session_id
    assert len(rig.launches()) == 1


def test_after_tmux_is_killed_the_tool_is_relaunched_with_resume(rig: Rig) -> None:
    first = rig.manager.ensure("p1", str(rig.root), "claude-code")
    rig.launches()
    rig.tmux.kill_session(first.tmux_session)
    assert rig.manager.status("p1", str(rig.root)).state == "stopped"
    again = rig.manager.ensure("p1", str(rig.root), "claude-code")
    assert again.state == "running" and again.last_action == "relaunched"
    assert again.session_id == first.session_id
    assert rig.launches()[-1].startswith(f"claude --resume {first.session_id} ")


def test_stop_ends_the_session_and_is_not_undone_by_a_reconcile(rig: Rig) -> None:
    rig.manager.ensure("p1", str(rig.root), "codex")
    info = rig.manager.stop("p1", str(rig.root))
    assert info.state == "stopped"
    assert not wanted(str(rig.root))


def test_status_of_a_project_that_never_had_a_session(rig: Rig) -> None:
    info = rig.manager.status("never", str(rig.root))
    assert info.state == "none" and info.last_action is None


def test_the_agent_gets_exactly_the_env_it_is_given(rig: Rig, tmp_path: Path) -> None:
    (rig.bin / "claude").write_text(f"#!/bin/sh\nenv > {tmp_path}/env.txt\nexec sleep 300\n")
    rig.manager.ensure("p1", str(rig.root), "claude-code")
    for _ in range(50):
        if (tmp_path / "env.txt").exists():
            break
        time.sleep(0.1)
    names = {line.split("=", 1)[0] for line in (tmp_path / "env.txt").read_text().splitlines()}
    assert {"PATH", "HOME"} <= names
    assert "CUTTLEFISH_SECRETS_KEY" not in names and "OPENROUTER_API_KEY" not in names


def test_a_missing_tmux_is_a_clear_error(tmp_path: Path) -> None:
    manager = SessionManager(
        Tmux(tmp_path / "s.sock", binary="no-such-tmux"), env_for=lambda _r, _b: {}
    )
    with pytest.raises(SessionError, match="needs tmux"):
        manager.ensure("p1", str(tmp_path), "claude-code")


def test_a_backend_with_no_interactive_tool_is_refused(rig: Rig) -> None:
    with pytest.raises(SessionError):
        rig.manager.ensure("p1", str(rig.root), "nope")


def test_resume_commands_per_backend(tmp_path: Path) -> None:
    root = str(tmp_path)
    assert launch_argv("claude-code", root=root, session_id="abc", resume=False) == [
        "claude", "--session-id", "abc", "--append-system-prompt", AGENT_NOTE,
    ]  # fmt: skip
    resumed = launch_argv("claude-code", root=root, session_id="abc", resume=True)
    assert (
        resumed[:2] == ["sh", "-c"]
        and "claude --resume abc --append-system-prompt" in resumed[2]
        and "|| exec claude --session-id abc --append-system-prompt" in resumed[2]
    )
    assert launch_argv("codex", root=root, session_id="x", resume=False) == ["codex"]
    codex = launch_argv("codex", root=root, session_id="x", resume=True)
    assert "codex resume --last" in codex[2]


def test_kopicode_resumes_the_newest_listed_session(tmp_path: Path) -> None:
    tool = tmp_path / "kopicode"
    rows = [
        {"id": "old", "started_at": "2026-08-01T00:00:00Z"},
        {"id": "new", "started_at": "2026-09-01T00:00:00Z"},
    ]
    tool.write_text("#!/bin/sh\n" + "".join(f"echo '{json.dumps(r)}'\n" for r in rows))
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    argv = launch_argv(
        "kopicode", root=str(tmp_path), session_id="", resume=True, kopicode_binary=str(tool)
    )
    assert argv == [str(tool), "-resume", "new"]
    none = launch_argv(
        "kopicode", root=str(tmp_path), session_id="", resume=False, kopicode_binary=str(tool)
    )
    assert none == [str(tool)]


def test_a_deep_home_gets_a_short_socket_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuttlefish.sessions import default_socket

    monkeypatch.setenv("HOME", "/h")
    assert default_socket() == Path("/h/.cuttlefish/tmux.sock")
    deep = tmp_path / ("d" * 60) / ("e" * 60)
    monkeypatch.setenv("HOME", str(deep))
    socket = default_socket()
    assert len(str(socket)) < 60 and socket.parent.name.startswith("cuttlefish-")
    assert default_socket() == socket  # the same HOME always maps to the same server
    monkeypatch.setenv("HOME", str(deep) + "x")
    assert default_socket() != socket
