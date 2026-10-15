"""Which Codex thread a project's role is continuing (CUT-92, ADR-0032).

Each round used to ``thread/start`` a new Codex thread, so a follow-up prompt began from scratch.
With ``CUTTLEFISH_CODEX_RESUME=1`` the thread id is kept here, in the project's own
``.cuttlefish/`` (next to the episodic store, never in satay's task arguments), and the next
round calls ``thread/resume`` on it. Codex keeps the conversation on disk, so the id survives a
daemon restart or a reboot.

A thread belongs to one *key*: the role's name, or ``default`` for a one-shot run. Two roles on
one root therefore never share a conversation. A handover (ADR-0030) starts a fresh agent on
purpose, so :func:`forget` drops the id there.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

#: ``CUTTLEFISH_CODEX_RESUME=1`` turns thread continuity on. Off, every round starts a thread.
RESUME_ENV = "CUTTLEFISH_CODEX_RESUME"
DEFAULT_KEY = "default"


def resume_enabled() -> bool:
    return os.environ.get(RESUME_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _path(root: str) -> Path:
    return Path(root) / ".cuttlefish" / "codex-threads.json"


def _read(root: str) -> dict[str, str]:
    try:
        data = json.loads(_path(root).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _write(root: str, threads: dict[str, str]) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(".tmp")
    scratch.write_text(json.dumps(threads, indent=2, sort_keys=True))
    scratch.replace(path)


def recall(root: str, key: str) -> str | None:
    """The thread id this key last used in ``root``, if any."""
    return _read(root).get(key)


def remember(root: str, key: str, thread_id: str) -> None:
    threads = _read(root)
    if threads.get(key) != thread_id:
        threads[key] = thread_id
        _write(root, threads)


def forget(root: str, key: str) -> None:
    """Drop the id, so the next round starts a fresh thread. Never raises: a missing or unwritable
    file means there is nothing to forget."""
    with contextlib.suppress(OSError):
        threads = _read(root)
        if threads.pop(key, None) is not None:
            _write(root, threads)
