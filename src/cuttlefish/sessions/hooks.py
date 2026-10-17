"""The settings file that gives a project's Claude Code session its hooks (ADR-0040).

Written to ``<root>/.cuttlefish/claude-settings.json`` at each launch and passed with
``claude --settings``, so the project's own ``.claude`` files are never touched (a project's own
hooks still run; they merge with these). Holds no secret: the hook script reads its token
from a file.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

HOOK_SCRIPT = Path(__file__).with_name("hook_script.py")

#: Events that only inform the daemon: they return at once, so a short limit is enough.
_QUICK_EVENTS = ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop")
_QUICK_TIMEOUT_S = 10
#: How much longer than the daemon's request window the script waits, and then the settings limit.
_SOCKET_MARGIN_S = 5
_SETTINGS_MARGIN_S = 15


def settings_path(root: str) -> Path:
    return Path(root) / ".cuttlefish" / "claude-settings.json"


def permission_mode(project_mode: str) -> str:
    """Claude Code's ``--permission-mode`` for a project's mode. Ask first and Standard both let
    Claude Code prompt (the daemon decides which prompts a person sees); Auto never prompts."""
    return "auto" if project_mode == "auto" else "manual"


def build_settings(project_id: str, window_s: float) -> dict[str, Any]:
    def command(timeout: float) -> str:
        return shlex.join(
            [
                sys.executable,
                "-I",
                str(HOOK_SCRIPT),
                "--project",
                project_id,
                "--timeout",
                str(int(timeout)),
            ]
        )

    held = window_s + _SOCKET_MARGIN_S
    hooks: dict[str, Any] = {
        "PermissionRequest": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": command(held),
                        "timeout": int(window_s + _SETTINGS_MARGIN_S),
                    }
                ]
            }
        ]
    }
    for event in _QUICK_EVENTS:
        hooks[event] = [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": command(_QUICK_TIMEOUT_S - 2),
                        "timeout": _QUICK_TIMEOUT_S,
                    }
                ]
            }
        ]
    return {"hooks": hooks}


def write_settings(root: str, project_id: str, window_s: float) -> Path:
    path = settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_settings(project_id, window_s), indent=2) + "\n")
    return path
