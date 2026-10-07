"""``cuttlefish doctor``: what is set up, and what is quietly wrong (ADR-0029 decision 4).

Every check is a pure function of what it is handed (an environment mapping, a ``which``,
a project list) so each can be tested without touching the machine. Credentials are only
ever reported by *name*: set or not, valid or not, never the value.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet

from cuttlefish.config import (
    CLAUDE_CODE_BIN_ENV,
    CODEX_BIN_ENV,
    KOPICODE_BIN_ENV,
    resolve_claude_code_binary,
    resolve_codex_binary,
    resolve_kopicode_binary,
)
from cuttlefish.delegate.subprocess_env import merge_env
from cuttlefish.projects.store import Project
from cuttlefish.secrets.store import SECRETS_KEY_ENV

Status = Literal["ok", "warn", "fail"]
_MARK = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}

#: Any value that still carries the ``.env.example`` placeholder text.
_PLACEHOLDER = "REPLACE_WITH"
_CREDENTIALS = ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "E2B_API_KEY")


@dataclass(frozen=True, slots=True)
class Check:
    status: Status
    name: str
    detail: str

    def render(self) -> str:
        return f"[{_MARK[self.status]}] {self.name}: {self.detail}"


def _version(binary: str) -> str:
    """First line of ``<binary> --version``, or why there is none. Run only on a binary
    already found on PATH, with a short timeout."""
    try:
        done = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=merge_env(None),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"version unknown ({type(exc).__name__})"
    first = (done.stdout or done.stderr).strip().splitlines()
    return first[0] if first else "version unknown"


def check_backends(
    environ: Mapping[str, str],
    which: Callable[[str], str | None] = shutil.which,
    version: Callable[[str], str] = _version,
) -> list[Check]:
    """Each backend's binary: on PATH or not, where, and its version. A missing one is only
    a warning: it matters only to a project that uses that backend (checked at team start)."""
    del environ  # the resolvers read os.environ; kept for a uniform signature
    checks = []
    for label, binary, env_name in (
        ("kopicode", resolve_kopicode_binary(), KOPICODE_BIN_ENV),
        ("claude-code", resolve_claude_code_binary(), CLAUDE_CODE_BIN_ENV),
        ("codex", resolve_codex_binary(), CODEX_BIN_ENV),
    ):
        found = which(binary)
        if found is None:
            checks.append(
                Check("warn", f"backend {label}", f"{binary!r} not on PATH (set {env_name})")
            )
        else:
            checks.append(Check("ok", f"backend {label}", f"{found} ({version(found)})"))
    return checks


def check_credentials(environ: Mapping[str, str]) -> list[Check]:
    """Credential *names* only. The secrets key is also checked for being a real Fernet key,
    since the `.env.example` placeholder looks set but is not (and starts a secrets store
    that cannot open)."""
    checks = []
    key = environ.get(SECRETS_KEY_ENV)
    if not key:
        checks.append(Check("ok", SECRETS_KEY_ENV, "not set (no project-scoped secrets store)"))
    elif _PLACEHOLDER in key:
        checks.append(
            Check(
                "fail",
                SECRETS_KEY_ENV,
                "still the .env.example placeholder: run `cuttlefish secrets generate-key`, "
                "or remove the line to run without a secrets store",
            )
        )
    else:
        try:
            Fernet(key.encode("ascii"))
        except (ValueError, TypeError):
            checks.append(Check("fail", SECRETS_KEY_ENV, "set but not a valid Fernet key"))
        else:
            checks.append(Check("ok", SECRETS_KEY_ENV, "set and valid"))
    for name in _CREDENTIALS:
        value = environ.get(name)
        if not value:
            checks.append(Check("ok", name, "not set"))
        elif _PLACEHOLDER in value:
            checks.append(Check("fail", name, "still the .env.example placeholder"))
        else:
            checks.append(Check("ok", name, "set"))
    return checks


def check_path(environ: Mapping[str, str], *, prefix: str | None = None) -> list[Check]:
    """PATH entries that mislead an agent: cuttlefish's own venv (scrubbed from the agent's
    environment, ADR-0029) and Windows directories under WSL."""
    entries = [e for e in environ.get("PATH", "").split(os.pathsep) if e]
    own = Path(prefix if prefix is not None else sys.prefix).resolve()
    checks = []
    if sys.prefix != sys.base_prefix or prefix is not None:
        inside = [e for e in entries if own in (p := Path(e).resolve()).parents or p == own]
        if inside:
            checks.append(
                Check(
                    "warn",
                    "PATH",
                    f"cuttlefish's own venv ({inside[0]}) leads it; agents get PATH without it",
                )
            )
    windows = [e for e in entries if e.startswith("/mnt/")]
    if windows:
        checks.append(
            Check(
                "warn",
                "PATH",
                f"{len(windows)} Windows entries (/mnt/...), e.g. {windows[0]}: an agent could "
                "run Windows python/npm by accident",
            )
        )
    return checks or [Check("ok", "PATH", "no misleading entries")]


def check_log(path: Path) -> list[Check]:
    if path.exists():
        return [Check("ok", "log file", f"{path} ({path.stat().st_size} bytes)")]
    parent = path.parent
    writable = os.access(parent if parent.exists() else _nearest_existing(parent), os.W_OK)
    if writable:
        return [Check("ok", "log file", f"{path} (created on first `cuttlefish serve`)")]
    return [Check("fail", "log file", f"{path} cannot be created: {parent} is not writable")]


def _nearest_existing(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def check_projects(projects: Iterable[Project]) -> list[Check]:
    checks = []
    for project in projects:
        root = Path(project.root)
        label = f"project {project.name}"
        if not root.is_dir():
            checks.append(Check("fail", label, f"root {root} does not exist"))
            continue
        problems = []
        if not (root / ".git").exists():
            problems.append("not a git repository")
        secrets_db = root / ".cuttlefish" / "secrets.db"
        if secrets_db.exists() and secrets_db.stat().st_size == 0:
            problems.append(
                f"{secrets_db} is empty: a start failed while opening the secrets store "
                f"(check {SECRETS_KEY_ENV}), safe to delete"
            )
        checks.append(
            Check("warn", label, "; ".join(problems)) if problems else Check("ok", label, f"{root}")
        )
    return checks


def run_checks(
    *, environ: Mapping[str, str], projects: Sequence[Project], log_path: Path
) -> list[Check]:
    return [
        *check_backends(environ),
        *check_credentials(environ),
        *check_path(environ),
        *check_log(log_path),
        *check_projects(projects),
    ]


def exit_code(checks: Iterable[Check]) -> int:
    return 1 if any(c.status == "fail" for c in checks) else 0
