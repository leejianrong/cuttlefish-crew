"""The loopback dashboard token, kept across restarts (CUT-106).

``cuttlefish serve`` on a loopback bind used to issue a fresh token on every start, so a
dashboard already open in a browser got a 401 on every poll after the daemon restarted and
only a Disconnect and a new paste got it back. The token now lives in one file under
``~/.cuttlefish`` (mode 0600) and is reused; delete the file to rotate it. This is the same
secret the daemon always printed at start, held where only the operator's own account reads it.
Re-authenticating the page automatically instead is a later card.
"""

from __future__ import annotations

import contextlib
import os
import secrets
from pathlib import Path

#: A token shorter than this was not written by us (satay's own is ~43 characters).
MIN_TOKEN_LENGTH = 32


def default_serve_token_path() -> Path:
    return Path.home() / ".cuttlefish" / "serve-token"


def _generate() -> str:
    return secrets.token_urlsafe(32)


def _read(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if len(text) < MIN_TOKEN_LENGTH or any(ch.isspace() for ch in text):
        return None
    return text


def load_or_create_token(path: Path) -> str:
    """The token saved at ``path``, or a new one saved there with mode 0600.

    A missing, unreadable or malformed file is replaced rather than trusted. Two daemons
    starting at once cannot end up with different tokens on disk: the file is created
    exclusively, and the loser reads what the winner wrote.
    """
    existing = _read(path)
    if existing is not None:
        with contextlib.suppress(OSError):
            path.chmod(0o600)
        return existing

    path.parent.mkdir(parents=True, exist_ok=True)
    token = _generate()
    # Malformed or unreadable leftovers only: a good file returned above.
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _read(path) or token
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    return token
