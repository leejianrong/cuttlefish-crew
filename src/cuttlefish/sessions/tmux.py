"""The little of tmux cuttlefish needs, on a socket of its own.

A dedicated ``-S <socket>`` keeps cuttlefish's sessions out of a person's own tmux (their
``tmux ls`` never shows them, and a scratch ``HOME`` gets a separate server). The tmux server
is a separate process from the daemon, so killing the daemon leaves every agent running; that
is the point (ADR-0032). All calls block briefly: callers run them with ``asyncio.to_thread``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


class TmuxError(RuntimeError):
    """tmux is missing, or refused a command."""


def session_name(project_id: str) -> str:
    """A tmux-safe name (no ``.`` or ``:``) for a project's session."""
    return "cf-" + _UNSAFE.sub("-", project_id)


def shell_session_name(project_id: str) -> str:
    """The name of a project's plain shell session, apart from its agent's (CUT-170)."""
    return "cf-shell-" + _UNSAFE.sub("-", project_id)


class Tmux:
    def __init__(self, socket: Path, binary: str = "tmux") -> None:
        self.socket = socket
        self.binary = binary

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _run(
        self, *args: str, check: bool = True, input: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        if not self.available():
            raise TmuxError(
                f"{self.binary!r} is not installed; the agent session needs tmux"
                " (for example: apt install tmux)"
            )
        self.socket.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [self.binary, "-S", str(self.socket), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
            input=input,
        )
        if check and result.returncode != 0:
            raise TmuxError(
                f"tmux {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        return result

    def has_session(self, name: str) -> bool:
        return self._run("has-session", "-t", f"={name}", check=False).returncode == 0

    def new_session(
        self,
        name: str,
        *,
        cwd: str,
        argv: Sequence[str],
        env: Mapping[str, str],
        width: int = 200,
        height: int = 50,
    ) -> None:
        """Start ``argv`` detached in ``cwd`` with *exactly* ``env`` (``env -i``), so the agent
        gets the allowlisted environment (ADR-0029) and not the tmux server's own."""
        command = ["env", "-i", *(f"{k}={v}" for k, v in env.items()), *argv]
        self._run(
            "new-session", "-d", "-s", name, "-c", cwd, "-x", str(width), "-y", str(height),
            *command,
        )  # fmt: skip
        # The dashboard's terminal view is the status line's only audience, and it has its own.
        # (`set-option` takes a plain session name; the `=` exact-match form is refused.)
        self._run("set-option", "-t", name, "status", "off")

    def kill_session(self, name: str) -> None:
        self._run("kill-session", "-t", f"={name}", check=False)

    # -- typing into a session (CUT-173) ------------------------------------------------------

    def paste(self, name: str, text: str) -> None:
        """Put ``text`` in the session's input as one bracketed paste, so a tool that asked for
        it (Claude Code does) reads it as pasted text and not as keystrokes: a newline in it is a
        line break, not Enter (``-r`` keeps it a newline, which tmux would make a carriage
        return). ``text`` is cleaned of control characters by the caller; a paste's end marker
        inside it would otherwise let the rest be read as keys."""
        buffer = f"cf-paste-{name}"
        self._run("load-buffer", "-b", buffer, "-", input=text)
        self._run("paste-buffer", "-p", "-r", "-d", "-b", buffer, "-t", f"={name}:")

    def press_enter(self, name: str) -> None:
        self._run("send-keys", "-t", f"={name}:", "Enter")

    def capture(self, name: str, lines: int = 15) -> str:
        """The last ``lines`` non-blank lines the session's screen shows."""
        shown = self._run("capture-pane", "-p", "-t", f"={name}:").stdout.splitlines()
        return "\n".join([line for line in shown if line.strip()][-lines:])
