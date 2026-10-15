"""Which conversation a project's role is continuing, per backend (CUT-92, CUT-93, ADR-0032).

Each round used to start a new agent conversation, so a follow-up prompt began from scratch. With
``CUTTLEFISH_RESUME=1`` the backend's own conversation id (a Codex thread, a Claude Code session)
is kept here, in the project's own ``.cuttlefish/`` (next to the episodic store, never in satay's
task arguments), and the next round resumes it with the tool's own mechanism (``thread/resume``,
``--resume``). The tool keeps the conversation on disk, so the id survives a daemon restart or a
reboot.

A conversation belongs to one *key*: the role's name, or ``default`` for a one-shot run. Two roles
on one root therefore never share one. A handover (ADR-0030) starts a fresh agent on purpose, so
:func:`forget_all` drops the id there. The file is
``<root>/.cuttlefish/conversations.json``: ``{backend: {key: id}}``.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

#: ``CUTTLEFISH_RESUME=1`` turns continuity on for every backend that has it. Off, every round
#: starts a new conversation. ``CUTTLEFISH_CODEX_RESUME`` (the first name, CUT-92) still works for
#: Codex alone.
RESUME_ENV = "CUTTLEFISH_RESUME"
LEGACY_ENVS = {"codex": "CUTTLEFISH_CODEX_RESUME"}
DEFAULT_KEY = "default"
_TRUE = ("1", "true", "yes", "on")


def resume_enabled(backend: str) -> bool:
    for name in (RESUME_ENV, LEGACY_ENVS.get(backend)):
        if name and os.environ.get(name, "").strip().lower() in _TRUE:
            return True
    return False


def _path(root: str) -> Path:
    return Path(root) / ".cuttlefish" / "conversations.json"


def _read(root: str) -> dict[str, dict[str, str]]:
    try:
        data = json.loads(_path(root).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        backend: {k: v for k, v in keys.items() if isinstance(k, str) and isinstance(v, str)}
        for backend, keys in data.items()
        if isinstance(backend, str) and isinstance(keys, dict)
    }


def _write(root: str, data: dict[str, dict[str, str]]) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(".tmp")
    scratch.write_text(json.dumps(data, indent=2, sort_keys=True))
    scratch.replace(path)


def recall(root: str, backend: str, key: str) -> str | None:
    """The conversation id this key last used with ``backend`` in ``root``, if any."""
    return _read(root).get(backend, {}).get(key)


def remember(root: str, backend: str, key: str, conversation_id: str) -> None:
    data = _read(root)
    keys = data.setdefault(backend, {})
    if keys.get(key) != conversation_id:
        keys[key] = conversation_id
        _write(root, data)


def forget(root: str, backend: str, key: str) -> None:
    """Drop one id, so the next round starts a new conversation. Never raises: a missing or
    unwritable file means there is nothing to forget."""
    with contextlib.suppress(OSError):
        data = _read(root)
        if data.get(backend, {}).pop(key, None) is not None:
            _write(root, data)


def forget_all(root: str, key: str) -> None:
    """Drop ``key``'s conversation for every backend (a handover starts a fresh agent)."""
    with contextlib.suppress(OSError):
        data = _read(root)
        dropped = [keys.pop(key, None) for keys in data.values()]
        if any(value is not None for value in dropped):
            _write(root, data)
