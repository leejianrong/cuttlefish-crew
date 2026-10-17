"""What a hook script needs to find the daemon (ADR-0040): where it is, and a token that opens only
the hook endpoint.

``~/.cuttlefish/daemon.json`` holds the address and pid, written once the daemon is listening and
removed when it exits, so a script started by a session that outlived a daemon restart finds the new
one. ``~/.cuttlefish/hook-token`` (mode 0600) is kept across restarts like the loopback token and
opens nothing but ``/api/hooks/...``: it can raise a Needs you card and cannot answer one.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from cuttlefish.fleet.serve_token import load_or_create_token


def default_home() -> Path:
    return Path.home() / ".cuttlefish"


def hook_token_path(home: Path | None = None) -> Path:
    return (home or default_home()) / "hook-token"


def daemon_info_path(home: Path | None = None) -> Path:
    return (home or default_home()) / "daemon.json"


def hook_token(home: Path | None = None) -> str:
    return load_or_create_token(hook_token_path(home))


def connect_host(host: str) -> str:
    """The address a local script uses for a daemon bound to ``host``."""
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def write_daemon_info(host: str, port: int, home: Path | None = None) -> Path:
    path = daemon_info_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"url": f"http://{connect_host(host)}:{port}", "pid": os.getpid()}, handle)
    tmp.replace(path)
    return path


def remove_daemon_info(home: Path | None = None) -> None:
    """Remove the file only if it is ours: a newer daemon may already have replaced it."""
    path = daemon_info_path(home)
    with contextlib.suppress(OSError, ValueError, KeyError):
        if json.loads(path.read_text()).get("pid") == os.getpid():
            path.unlink()
