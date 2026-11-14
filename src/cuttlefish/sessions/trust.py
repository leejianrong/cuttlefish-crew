"""Answering Claude Code's "trust this folder" dialog for a worktree cuttlefish made (CUT-182).

A goal's worktree (``<root>/.cuttlefish/worktrees/<name>``, ADR-0048) is a folder Claude Code has
never seen, so it opens on a dialog whose cursor is on "No, exit"; nothing answers it and an
unattended run stops before any work. Claude Code has no flag or project setting for this (its
per-folder trust lives in the person's own ``~/.claude.json``, which cuttlefish does not edit), so
this reads the screen and picks "Yes, I trust this folder", and only when:

* the folder is a direct child of ``<root>/.cuttlefish/worktrees`` (one cuttlefish itself made), and
* the dialog's own "Accessing workspace" line names exactly that folder.

Anything else (a project root, another folder, a screen that does not match) is left to the person
in the terminal. It selects the option, checks the cursor is on "Yes", and only then presses Enter.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

_QUESTION = re.compile(r"Is this a project you created or one you trust", re.I)
_PATH = re.compile(r"Accessing workspace:\s*(.*?)\s*Quick safety check", re.S)
#: The selection cursor: U+276F in Claude Code, U+203A in Codex, or a plain ``>``.
_CURSOR = f"[{chr(0x276F)}{chr(0x203A)}>]"
_ON_YES = re.compile(_CURSOR + r"\s*Yes, I trust this folder")
_ON_NO = re.compile(_CURSOR + r"\s*No, exit")


_RETRY_S = 1.5


class _Keys(Protocol):
    def capture(self, name: str, lines: int = 15) -> str: ...
    def send_key(self, name: str, key: str) -> None: ...
    def has_session(self, name: str) -> bool: ...


def is_goal_worktree(root: str, folder: str) -> bool:
    """Whether ``folder`` is directly inside ``root``'s worktrees folder (``create``'s place)."""
    base = (Path(root) / ".cuttlefish" / "worktrees").resolve()
    return Path(folder).resolve().parent == base


def dialog_folder(screen: str) -> str | None:
    """The folder a trust dialog on ``screen`` asks about, or ``None`` when there is none. The
    path may wrap, so the lines between its label and the next sentence are joined."""
    if not _QUESTION.search(screen):
        return None
    found = _PATH.search(screen)
    return "".join(found.group(1).split()) if found else None


def answer(
    keys: _Keys,
    name: str,
    root: str,
    folder: str,
    *,
    timeout_s: float = 45.0,
    interval_s: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Wait up to ``timeout_s`` for the trust dialog of ``folder`` and accept it; ``True`` when it
    was answered. A folder that is not one of ``root``'s goal worktrees is never answered."""
    if not is_goal_worktree(root, folder):
        return False
    wanted = "".join(str(Path(folder).resolve()).split())
    deadline = clock() + timeout_s
    moved_at: float | None = None
    while clock() < deadline:
        if not keys.has_session(name):
            return False
        screen = keys.capture(name, lines=20)
        shown = dialog_folder(screen)
        if shown is not None:
            if shown != wanted:
                return False
            if _ON_YES.search(screen):
                keys.send_key(name, "Enter")
                return True
            # A TUI that is still starting can miss a key, so the cursor is moved again only after
            # a wait; Enter is only ever sent for a screen that shows the cursor on "Yes".
            if _ON_NO.search(screen) and (moved_at is None or clock() - moved_at >= _RETRY_S):
                keys.send_key(name, "Down")
                moved_at = clock()
        sleep(interval_s)
    return False
