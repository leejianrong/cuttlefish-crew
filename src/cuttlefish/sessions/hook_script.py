#!/usr/bin/env python3
"""The command Claude Code runs for a hook of a project's agent session (ADR-0040).

Standard library only, so it starts fast and runs with any Python. It reads the hook's JSON on
stdin, sends it to the running daemon, and prints what the daemon decided. **Anything wrong
prints nothing and exits 0**: no daemon, a bad token, a timeout, an error of any kind. Claude
Code then shows its own permission dialog, which is the fallback. It never prints `allow` unless
the daemon said so in this very response.

The daemon is found through ``~/.cuttlefish/daemon.json`` and trusted with ``~/.cuttlefish/hook-
token`` (mode 0600), so a daemon that restarted on another port still answers a session that
kept running."""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

#: The longest a single string in `tool_input` is sent. A `Write` can carry a whole file.
_MAX_STRING = 4000


def _clip(value: object, depth: int = 0) -> object:
    if isinstance(value, str):
        return value if len(value) <= _MAX_STRING else value[:_MAX_STRING] + "…"
    if depth > 4:
        return None
    if isinstance(value, list):
        return [_clip(v, depth + 1) for v in value[:50]]
    if isinstance(value, dict):
        return {str(k): _clip(v, depth + 1) for k, v in list(value.items())[:50]}
    return value


def _arg(argv: list[str], name: str, default: str) -> str:
    return (
        argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default
    )


def main(argv: list[str]) -> int:
    try:
        project = _arg(argv, "--project", "")
        timeout = float(_arg(argv, "--timeout", "30"))
        payload = json.load(sys.stdin)
        if not project or not isinstance(payload, dict):
            return 0
        home = Path.home() / ".cuttlefish"
        url = str(json.loads((home / "daemon.json").read_text())["url"]).rstrip("/")
        token = (home / "hook-token").read_text().strip()
        slim = {
            key: _clip(payload.get(key))
            for key in (
                "hook_event_name",
                "session_id",
                "transcript_path",
                "cwd",
                "permission_mode",
                "tool_name",
                "tool_input",
                "tool_use_id",
            )
            if key in payload
        }
        request = urllib.request.Request(
            f"{url}/api/hooks/claude-code/{project}",
            data=json.dumps(slim).encode(),
            headers={"content-type": "application/json", "x-cuttlefish-hook-token": token},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            answer = json.load(response)
        if payload.get("hook_event_name") != "PermissionRequest" or not isinstance(answer, dict):
            return 0
        behavior = answer.get("decision")
        if behavior not in ("allow", "deny"):
            return 0
        decision: dict[str, str] = {"behavior": behavior}
        message = answer.get("message")
        if behavior == "deny" and isinstance(message, str) and message:
            decision["message"] = message[:500]
        print(
            json.dumps(
                {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}
            )
        )
    except BaseException:  # anything at all: stay silent, the terminal dialog is the fallback
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
