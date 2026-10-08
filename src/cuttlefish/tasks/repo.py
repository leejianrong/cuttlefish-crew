"""Read a repository's own state for a handover (ADR-0030).

A handover is written by a model that sees the journal, which lists tool calls but not their
output, so it cannot know whether a commit landed or a file is still modified. Git can say. This
reads ``git log`` and ``git status`` for the checkpoint, so what the next session is told about
the repository is a fact cuttlefish read, not something a summariser inferred.

A durable task (a workflow body must be deterministic and this reads the world), not a side
effect: reading changes nothing, and a replay reuses the recorded text.
"""

from __future__ import annotations

import asyncio
import os

import satay

#: The heading the state block starts with; `handover` finds and strips it from an older summary.
REPO_STATE_MARKER = "Repository state when this checkpoint was written"
_TIMEOUT_S = 10.0
_LOG_LINES = 15
_STATUS_LINES = 25
_MAX_LINE = 160


async def _git(root: str, *args: str) -> str | None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "--no-pager",
            "-c",
            "core.quotepath=false",
            "-C",
            root,
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        out, _ = await asyncio.wait_for(process.communicate(), _TIMEOUT_S)
    except (OSError, TimeoutError):
        return None
    return out.decode("utf-8", "replace") if process.returncode == 0 else None


def _lines(text: str, limit: int) -> list[str]:
    lines = [line[:_MAX_LINE] for line in text.splitlines() if line.strip()]
    more = len(lines) - limit
    return lines[:limit] + ([f"... and {more} more"] if more > 0 else [])


@satay.task()
async def read_repo_state(root: str) -> str:
    """The recent commits and uncommitted changes under ``root`` as a short block, or ``""``
    when ``root`` is not a git work tree (or git is not there): no state is better than a guess."""
    log = await _git(root, "log", f"-{_LOG_LINES + 1}", "--oneline", "--no-decorate")
    if log is None:
        return ""
    status = await _git(root, "status", "--short") or ""
    commits = _lines(log, _LOG_LINES) or ["(no commits yet)"]
    changes = _lines(status, _STATUS_LINES) or ["(none: the working tree is clean)"]
    return "\n".join(
        [
            f"{REPO_STATE_MARKER} (read from git by cuttlefish, not written by the agent):",
            "Recent commits, newest first:",
            *commits,
            "Uncommitted changes:",
            *changes,
        ]
    )
