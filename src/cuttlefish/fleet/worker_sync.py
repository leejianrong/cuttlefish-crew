"""The files a person wants on every worker: skills, instruction files and MCP config (CUT-215).

They live in one folder on the controller, `~/.cuttlefish/worker-sync/` (or
`$CUTTLEFISH_WORKER_SYNC_DIR`):

    skills/<name>/SKILL.md (and any files beside it)   a skill, installed for Claude Code on each
    worker
    instructions/<name>.md                              an AGENTS-style file, placed in each
    team's checkout
    mcp.json                                            MCP server config, with its keys stripped
    (below)

`load` reads that folder into the set a worker is sent, and says which files it refused and why. The
rule is "never copy secrets", so it errs towards refusing: a file named like a credential, a file
that is
not text, one that holds a private key, a provider key or a `token = <long value>` line, is not
sent and is
listed with its reason (never its content). In `mcp.json` only `command`, `args`, `url` and
`type` of each
server are kept: `env` and `headers`, where MCP config holds keys, are dropped, so a server that
needs one is
set up on the worker by hand. The worker's copy is compared with this set by digest (`drift`),
and the
difference is reported next to the tool drift on Devices.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_FILES = 200
MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 1_000_000
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
_REL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,150}$")
MCP_SERVER_KEYS = ("command", "args", "url", "type")

_SECRET_FILE = re.compile(
    r"^(\.env.*|.*\.(pem|key|p12|pfx|kdbx|keystore|token)|id_(rsa|dsa|ecdsa|ed25519)"
    r"|secrets?[._-].*|.*[._-]secrets?\..*|credentials?(\..*)?|\.netrc|\.npmrc|\.pypirc)$",
    re.IGNORECASE,
)
_SECRET_TEXT = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"\b(sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
        r"|xox[abposr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}"
        r"|glpat-[A-Za-z0-9_-]{15,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.)"
    ),
    re.compile(
        r"(?i)\b[\w-]*(api[_-]?key|secret|token|passw(or)?d|credential)[\w-]*[\"']?\s*[:=]\s*"
        r"[\"']?[A-Za-z0-9/+_=.-]{16,}"
    ),
]


@dataclass(slots=True)
class SyncSet:
    #: `relative path -> bytes`, in the form a worker receives (an `mcp.json` already stripped).
    files: dict[str, bytes] = field(default_factory=dict)
    #: `(relative path, why)` for each file left out; never the content.
    refused: list[tuple[str, str]] = field(default_factory=list)
    #: What was removed from a kept file (`mcp.json`'s `env` and `headers`), for a person to read.
    notes: list[str] = field(default_factory=list)

    def manifest(self) -> dict[str, str]:
        """`path -> sha256`: what a worker holds when it is up to date."""
        return {path: hashlib.sha256(data).hexdigest() for path, data in sorted(self.files.items())}


def secret_reason(name: str, data: bytes) -> str | None:
    """Why this file must not be sent, or `None`. The reason names the kind of thing, not its
    value."""
    if _SECRET_FILE.match(name.rsplit("/", 1)[-1]):
        return "named like a credential file"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "not a text file"
    if "\x00" in text:
        return "not a text file"
    for pattern in _SECRET_TEXT:
        if pattern.search(text):
            return "looks like it holds a secret"
    return None


def strip_mcp(data: bytes) -> tuple[bytes | None, str]:
    """`mcp.json` reduced to what is safe to copy, or `None` and why not."""
    try:
        body = json.loads(data)
        servers = body["mcpServers"]
        if not isinstance(servers, dict):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        return None, 'not an MCP config (a JSON object with "mcpServers")'
    kept: dict[str, Any] = {}
    dropped: set[str] = set()
    for name, server in servers.items():
        if not isinstance(server, dict) or not _NAME.match(str(name)):
            return None, "a server entry is not a plain name and object"
        kept[name] = {k: server[k] for k in MCP_SERVER_KEYS if k in server}
        dropped |= {k for k in ("env", "headers") if k in server}
    text = json.dumps({"mcpServers": kept}, indent=2, sort_keys=True) + "\n"
    if secret_reason("mcp.json", text.encode()):
        return None, "a command or argument looks like it holds a secret"
    note = f"mcp.json: dropped {' and '.join(sorted(dropped))} from the servers" if dropped else ""
    return text.encode(), note


def load(folder: Path) -> SyncSet:
    found = SyncSet()
    if not folder.is_dir():
        return found
    total = 0
    candidates: list[tuple[str, Path]] = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and not path.is_symlink():
            candidates.append((path.relative_to(folder).as_posix(), path))
    for rel, path in candidates:
        if not allowed_path(rel):
            found.refused.append(
                (rel, "not under skills/<name>/, instructions/<name>.md or mcp.json")
            )
            continue
        size = path.stat().st_size
        if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES or len(found.files) >= MAX_FILES:
            found.refused.append((rel, "too large, or too many files"))
            continue
        data = path.read_bytes()
        # mcp.json keeps its keys in the fields that are dropped before it is judged.
        reason = None if rel == "mcp.json" else secret_reason(rel, data)
        if reason is not None:
            found.refused.append((rel, reason))
            continue
        if rel == "mcp.json":
            stripped, note = strip_mcp(data)
            if stripped is None:
                found.refused.append((rel, note))
                continue
            if note:
                found.notes.append(note)
            data = stripped
        found.files[rel] = data
        total += len(data)
    return found


def allowed_path(rel: str) -> bool:
    """Is this a path a sync set may hold: `mcp.json`, `instructions/<n>.md` or `skills/<n>/...`?"""
    parts = rel.split("/")
    if ".." in parts or not _REL.match(rel):
        return False
    if rel == "mcp.json":
        return True
    if parts[0] == "instructions":
        return len(parts) == 2 and parts[1].endswith(".md") and bool(_NAME.match(parts[1]))
    return parts[0] == "skills" and len(parts) >= 3 and bool(_NAME.match(parts[1]))


def default_folder(state_dir: Path) -> Path:
    return Path(os.environ.get("CUTTLEFISH_WORKER_SYNC_DIR") or state_dir / "worker-sync")


class SyncCache:
    """`load`, kept for a few seconds: Devices asks for the comparison on every refresh."""

    def __init__(self, folder: Path, keep: float = 5.0) -> None:
        self.folder, self._keep = folder, keep
        self._at = -1e9
        self._set = SyncSet()

    def get(self) -> SyncSet:
        now = time.monotonic()
        if now - self._at >= self._keep:
            self._set, self._at = load(self.folder), now
        return self._set


def differences(have: dict[str, str], want: dict[str, str], limit: int = 8) -> list[str]:
    """What a worker holding `have` lacks of `want` (`path -> sha256`), each named, a few at
    most."""
    found = [f"{p} not synced" for p in want if p not in have]
    found += [f"{p} out of date" for p, sha in want.items() if p in have and have[p] != sha]
    found += [f"{p} was removed on the controller" for p in have if p not in want]
    return found[:limit] + ([f"and {len(found) - limit} more files"] if len(found) > limit else [])
