"""Unit: the settings file for a Claude Code session's hooks, and how it is launched with it.

CUT-127, ADR-0040."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from cuttlefish.fleet import hook_files
from cuttlefish.sessions.hooks import HOOK_SCRIPT, build_settings, permission_mode, write_settings
from cuttlefish.sessions.manager import launch_argv


def test_the_permission_hook_is_held_and_the_others_return_quickly() -> None:
    hooks = build_settings("p1", 600)["hooks"]
    assert set(hooks) == {
        "PermissionRequest",
        "PreToolUse",
        "PostToolUse",
        "UserPromptSubmit",
        "Stop",
    }
    held = hooks["PermissionRequest"][0]["hooks"][0]
    assert held["type"] == "command" and held["timeout"] == 615  # the window plus fifteen seconds
    for event in ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop"):
        assert hooks[event][0]["hooks"][0]["timeout"] == 10


def test_the_timeouts_nest_so_a_hook_always_exits_by_itself() -> None:
    window = 600
    held = build_settings("p1", window)["hooks"]["PermissionRequest"][0]["hooks"][0]
    argv = shlex.split(held["command"])
    socket_timeout = int(argv[argv.index("--timeout") + 1])
    assert window < socket_timeout < held["timeout"]  # the daemon answers first, then the script


def test_the_command_runs_our_script_with_the_project_and_holds_no_secret() -> None:
    settings = json.dumps(build_settings("proj-7", 600))
    command = shlex.split(build_settings("proj-7", 600)["hooks"]["Stop"][0]["hooks"][0]["command"])
    assert command[:3] == [sys.executable, "-I", str(HOOK_SCRIPT)]
    assert command[command.index("--project") + 1] == "proj-7"
    assert "token" not in settings.lower()


def test_the_file_is_written_inside_the_projects_own_cuttlefish_folder(tmp_path: Path) -> None:
    path = write_settings(str(tmp_path), "p1", 600)
    assert path == tmp_path / ".cuttlefish" / "claude-settings.json"
    assert json.loads(path.read_text()) == build_settings("p1", 600)
    assert not (tmp_path / ".claude").exists()  # the project's own files are never touched


@pytest.mark.parametrize(
    ("project_mode", "claude_mode"),
    [("ask-first", "manual"), ("standard", "manual"), ("auto", "auto")],
)
def test_the_projects_mode_picks_claude_codes_permission_mode(
    project_mode: str, claude_mode: str
) -> None:
    assert permission_mode(project_mode) == claude_mode


def test_a_new_session_is_launched_with_the_settings_and_the_mode() -> None:
    extra = ["--settings", "/r/.cuttlefish/claude-settings.json", "--permission-mode", "manual"]
    argv = launch_argv("claude-code", root="/r", session_id="sid", resume=False, claude_args=extra)
    assert argv[:3] == ["claude", "--session-id", "sid"] and argv[-4:] == extra


def test_a_resumed_session_gets_them_too_and_falls_back_to_a_fresh_start_with_them() -> None:
    extra = ["--settings", "/r/s.json", "--permission-mode", "auto"]
    argv = launch_argv("claude-code", root="/r", session_id="sid", resume=True, claude_args=extra)
    assert argv[:2] == ["sh", "-c"]
    script = argv[2]
    assert script.count("--settings /r/s.json --permission-mode auto") == 2  # resume and fallback


def test_a_session_without_hooks_is_launched_as_before() -> None:
    argv = launch_argv("claude-code", root="/r", session_id="sid", resume=False)
    assert "--settings" not in argv and "--permission-mode" not in argv
    resumed = launch_argv("claude-code", root="/r", session_id="sid", resume=True)
    assert "--settings" not in resumed[2]


def test_the_daemons_address_is_written_for_the_script_and_removed_only_by_its_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = hook_files.write_daemon_info("0.0.0.0", 8420, tmp_path)
    assert (
        json.loads(path.read_text())["url"] == "http://127.0.0.1:8420"
    )  # a script cannot dial 0.0.0.0
    assert hook_files.write_daemon_info("100.64.0.9", 8421, tmp_path) == path
    assert json.loads(path.read_text())["url"] == "http://100.64.0.9:8421"
    monkeypatch.setattr("os.getpid", lambda: 999_999)  # another daemon's cleanup
    hook_files.remove_daemon_info(tmp_path)
    assert path.exists()  # not ours, left alone
    monkeypatch.undo()
    hook_files.remove_daemon_info(tmp_path)
    assert not path.exists()


def test_the_hook_token_is_kept_across_restarts_and_private(tmp_path: Path) -> None:
    first = hook_files.hook_token(tmp_path)
    assert hook_files.hook_token(tmp_path) == first and len(first) >= 32
    assert (hook_files.hook_token_path(tmp_path).stat().st_mode & 0o777) == 0o600
