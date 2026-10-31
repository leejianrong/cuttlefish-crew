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


# -- the plain shell (CUT-170) ------------------------------------------------------------------


def _shell_text(rig: Rig, name: str) -> str:
    return rig.tmux._run("capture-pane", "-p", "-t", name).stdout


def _type_and_wait(rig: Rig, name: str, line: str, needle: str) -> str:
    for _ in range(50):  # keys sent while the shell starts can be dropped: wait for its prompt
        if "$" in _shell_text(rig, name):
            break
        time.sleep(0.1)
    rig.tmux._run("send-keys", "-t", name, line, "Enter")
    for _ in range(50):
        shown = _shell_text(rig, name)
        if needle in shown:
            return shown
        time.sleep(0.1)
    raise AssertionError(f"never saw {needle!r} in {shown!r}")


def test_the_shell_is_its_own_session_in_the_project_folder_and_needs_no_agent(rig: Rig) -> None:
    name = rig.manager.ensure_shell("p1", str(rig.root))
    assert name == "cf-shell-p1"
    assert rig.manager.status("p1", str(rig.root)).state == "none"  # not the agent's session
    _type_and_wait(rig, name, "echo cwd=$(pwd)", f"cwd={rig.root}")


def test_the_shell_gets_the_filtered_environment_and_no_project_secret(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEAKY_DAEMON_VALUE", "daemon-only")  # in the test process, not given out
    name = rig.manager.ensure_shell("p1", str(rig.root))
    shown = _type_and_wait(rig, name, "echo leak=[${LEAKY_DAEMON_VALUE-absent}]", "leak=[absent]")
    assert "daemon-only" not in shown


def test_ensure_shell_again_reuses_it_and_a_stopped_shell_starts_fresh(rig: Rig) -> None:
    name = rig.manager.ensure_shell("p1", str(rig.root))
    _type_and_wait(rig, name, "SENTINEL=kept", "SENTINEL=kept")
    assert rig.manager.ensure_shell("p1", str(rig.root)) == name
    _type_and_wait(rig, name, "echo again=${SENTINEL}", "again=kept")
    rig.manager.stop_shell("p1")
    assert not rig.tmux.has_session(name)
    rig.manager.ensure_shell("p1", str(rig.root))
    _type_and_wait(rig, name, "echo fresh=[${SENTINEL-unset}]", "fresh=[unset]")


# -- a goal as the first message (CUT-171) ------------------------------------------------------


def test_a_goal_is_the_first_message_of_a_fresh_claude_or_codex_session(rig: Rig) -> None:
    root = str(rig.root)
    claude = launch_argv("claude-code", root=root, session_id="abc", resume=False, prompt="Fix it")
    assert claude[-1] == "Fix it" and claude[:3] == ["claude", "--session-id", "abc"]
    assert launch_argv("codex", root=root, session_id="x", resume=False, prompt="Fix it") == [
        "codex",
        "Fix it",
    ]


def test_a_goal_reaches_a_resumed_session_as_one_quoted_argument(rig: Rig) -> None:
    root = str(rig.root)
    resumed = launch_argv(
        "claude-code", root=root, session_id="abc", resume=True, prompt='it\'s "quoted" $(x)'
    )
    assert resumed[:2] == ["sh", "-c"]
    # Both the resume and the fresh fallback carry it, quoted so the shell reads it as one word.
    assert resumed[2].count("'it'\"'\"'s \"quoted\" $(x)'") == 2
    codex = launch_argv("codex", root=root, session_id="x", resume=True, prompt="go")
    assert codex[2] == "codex resume --last go || exec codex go"


def test_a_goal_that_looks_like_a_flag_cannot_become_one(rig: Rig) -> None:
    root = str(rig.root)
    argv = launch_argv(
        "claude-code",
        root=root,
        session_id="a",
        resume=False,
        prompt="--dangerously-skip-permissions",
    )
    assert argv[-1] == "Goal: --dangerously-skip-permissions"


def test_kopicode_has_no_first_message_so_a_goal_is_refused_not_dropped(rig: Rig) -> None:
    with pytest.raises(SessionError, match="does not take a first message"):
        launch_argv("kopicode", root=str(rig.root), session_id="x", resume=False, prompt="go")


def test_ensure_with_a_goal_starts_the_tool_with_it_and_refuses_a_running_one(rig: Rig) -> None:
    rig.manager.ensure("p1", str(rig.root), "claude-code", prompt="Build the thing")
    assert rig.launches()[0].endswith("Build the thing")
    with pytest.raises(SessionError, match="already running"):
        rig.manager.ensure("p1", str(rig.root), "claude-code", prompt="Another")


def test_the_goal_is_recorded_beside_the_session_and_read_back(rig: Rig) -> None:
    from cuttlefish.sessions.manager import read_goal, write_goal

    assert read_goal(str(rig.root)) is None
    written = write_goal(str(rig.root), "Ship it", "claude-code")
    assert read_goal(str(rig.root)) == written
    assert written["text"] == "Ship it" and written["backend"] == "claude-code"


def test_a_terminal_command_runs_in_tmux_with_no_status_integration(rig: Rig) -> None:
    """ADR-0047: the command runs as given, nothing is resumed, and the record says it asks
    nowhere cuttlefish can see."""
    command = f"echo mytool $PWD >> {rig.log}; exec sleep 300"
    info = rig.manager.ensure("p1", str(rig.root), "ignored", command=command)
    assert info.state == "running" and info.last_action == "started"
    assert info.backend == "terminal" and info.asks == "none" and info.session_id is None
    assert rig.launches() == [f"mytool {rig.root}"]
    assert wanted(str(rig.root))
    again = rig.manager.ensure("p1", str(rig.root), "ignored", command=command)
    assert again.last_action == "reattached"
    assert rig.launches() == [f"mytool {rig.root}"]


def test_a_terminal_command_starts_again_without_a_resume_after_it_is_gone(rig: Rig) -> None:
    command = f"echo run >> {rig.log}; exec sleep 300"
    rig.manager.ensure("p1", str(rig.root), "x", command=command)
    rig.manager.stop("p1", str(rig.root))
    info = rig.manager.ensure("p1", str(rig.root), "x", command=command)
    assert info.last_action == "started"
    time.sleep(0.5)
    assert rig.log.read_text().splitlines() == ["run", "run"]


def test_a_terminal_command_refuses_a_first_message_and_an_empty_command(rig: Rig) -> None:
    with pytest.raises(SessionError, match="cannot be given a goal"):
        rig.manager.ensure("p1", str(rig.root), "x", command="sleep 300", prompt="do it")
    with pytest.raises(SessionError, match="empty"):
        rig.manager.ensure("p1", str(rig.root), "x", command="  ")
    assert rig.manager.status("p1", str(rig.root)).state == "none"
