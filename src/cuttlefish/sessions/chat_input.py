"""Typing into a live agent session from the chat box (CUT-173, ADR-0044).

The text goes in as one bracketed paste and then Enter, never as raw keystrokes (a newline in a
message must not submit half of it), and never while a dialog is open on the screen: a dialog
reads the next keys as its answer, so a message typed into one could approve something. Whether
one is open comes from two places that can each be wrong, so either is enough to refuse:
the session's hooks (a held permission card, or a dialog the terminal was left to show) and the
last lines of the screen.

The screen patterns are Claude Code's wording as far as it is known and are a safety net, not a
parser; a dialog they miss is the hooks' to catch, and one both miss is the person's to answer in
the terminal. Checked against Claude Code 2.1.308 for the trust-folder dialog (CUT-173 notes in
``agent_docs/known-gaps.md``); a permission dialog's wording is still assumed.
"""

from __future__ import annotations

import re

MAX_MESSAGE_CHARS = 8000

#: C0 controls (and DEL) other than newline and tab. An ESC in a message could close the paste
#: early (``ESC [ 2 0 1 ~``) and turn the rest into keystrokes.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_DIALOG_LINES = (
    re.compile(r"Esc to cancel", re.I),
    re.compile(r"[\u276f>]\s*1\.\s"),  # a numbered choice with the cursor on the first
    re.compile(
        r"Do you trust|trust this folder|trust the files|project you created or one you trust", re.I
    ),
    re.compile(r"Do you want to (proceed|make this edit|create|allow)", re.I),
)


class MessageError(ValueError):
    """The message cannot be sent as it is."""


def clean_message(text: str) -> str:
    """``text`` ready to paste: line endings made ``\\n``, control characters removed, trimmed.
    Raises :class:`MessageError` when nothing is left or it is too long."""
    cleaned = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not cleaned:
        raise MessageError("Say something to send.")
    if len(cleaned) > MAX_MESSAGE_CHARS:
        raise MessageError(
            f"A message is at most {MAX_MESSAGE_CHARS} characters; put the rest in a file."
        )
    return cleaned


def dialog_open(screen: str) -> bool:
    """Whether the last lines of a session's screen look like a dialog waiting for a choice."""
    return any(pattern.search(screen) for pattern in _DIALOG_LINES)
