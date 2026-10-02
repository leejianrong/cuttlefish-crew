"""``cuttlefish init`` -- guided first-run setup (KAN-1808).

Checks what a first run needs (an agent CLI on ``PATH`` and plausibly logged in, and
whether cuttlefish's own optional summarising key is set), registers the current
repo as a :class:`~cuttlefish.projects.store.Project`, proposes roles, and prints the
exact next command. Everything environment-dependent (``PATH`` lookup, env, home
directory) is passed in so the checks are testable without a real machine.

"Logged in" is a best-effort guess -- an env credential or the CLI's own on-disk
login directory existing -- never a read of a credential file's contents.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from cuttlefish.config import (
    AGENT_BACKEND_ENV,
    CLAUDE_CODE_BIN_ENV,
    CODEX_BIN_ENV,
    DEFAULT_CLAUDE_CODE_BIN,
    DEFAULT_CODEX_BIN,
    DEFAULT_KOPICODE_BIN,
    KOPICODE_BIN_ENV,
    LLM_PROVIDER_ENV,
)
from cuttlefish.projects.store import Project, ProjectStore, RoleDefinition

BACKENDS = ("kopicode", "claude-code", "codex")

#: Proposed when the operator names no role of their own: the smallest team that
#: shows what cuttlefish is for (one role writes, one checks).
DEFAULT_ROLES = (
    RoleDefinition(name="builder", persona="You implement the task with small, focused changes."),
    RoleDefinition(name="reviewer", persona="You review the repo's recent changes for bugs."),
)

_BINARY_ENVS = {
    "kopicode": (KOPICODE_BIN_ENV, DEFAULT_KOPICODE_BIN),
    "claude-code": (CLAUDE_CODE_BIN_ENV, DEFAULT_CLAUDE_CODE_BIN),
    "codex": (CODEX_BIN_ENV, DEFAULT_CODEX_BIN),
}


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str


def backend_binary(backend: str, env: Mapping[str, str]) -> str:
    env_name, default = _BINARY_ENVS[backend]
    return env.get(env_name, default)


def detect_backend(env: Mapping[str, str], which: Callable[[str], str | None]) -> str | None:
    """The configured backend, else the first one whose CLI is on ``PATH``."""
    configured = env.get(AGENT_BACKEND_ENV)
    if configured in BACKENDS:
        return configured
    for backend in BACKENDS:
        if which(backend_binary(backend, env)):
            return backend
    return None


def _login_hint(backend: str, env: Mapping[str, str], home: Path) -> Check:
    if backend == "kopicode":
        keyed = bool(env.get("OPENROUTER_API_KEY") or env.get("ANTHROPIC_API_KEY"))
        return Check(
            "kopicode credential",
            keyed,
            "OPENROUTER_API_KEY/ANTHROPIC_API_KEY set"
            if keyed
            else "set OPENROUTER_API_KEY or ANTHROPIC_API_KEY",
        )
    if backend == "claude-code":
        ok = bool(env.get("ANTHROPIC_API_KEY")) or (home / ".claude").is_dir()
        return Check(
            "claude login",
            ok,
            "found" if ok else "run `claude` once to log in, or set ANTHROPIC_API_KEY",
        )
    ok = (home / ".codex").is_dir()
    return Check("codex login", ok, "found" if ok else "run `codex login`")


def check_prerequisites(
    backend: str,
    *,
    env: Mapping[str, str],
    which: Callable[[str], str | None],
    home: Path,
) -> list[Check]:
    binary = backend_binary(backend, env)
    found = which(binary)
    checks = [
        Check(
            "uv", which("uv") is not None, "on PATH" if which("uv") else "install from astral.sh/uv"
        ),
        Check(
            f"{backend} CLI",
            found is not None,
            found or f"{binary!r} not on PATH; install it or set {_BINARY_ENVS[backend][0]}",
        ),
        _login_hint(backend, env, home),
    ]
    has_key = bool(env.get("OPENROUTER_API_KEY"))
    replay = env.get(LLM_PROVIDER_ENV) == "replay"
    checks.append(
        Check(
            "summarising key (optional)",
            True,
            "OPENROUTER_API_KEY set"
            if has_key
            else "replay provider"
            if replay
            else "not set: only needed if a long run reaches a handover summary "
            "(or set CUTTLEFISH_LLM_PROVIDER=replay)",
        )
    )
    return checks


def register_or_reuse(
    store: ProjectStore, *, name: str, root: Path, roles: tuple[RoleDefinition, ...]
) -> tuple[Project, bool]:
    """Register `root`, or return the project already registered there (re-running
    ``init`` is safe and never duplicates or overwrites). Returns ``(project, created)``."""
    resolved = str(root.resolve())
    for project in store.list():
        if project.root == resolved:
            return project, False
    return store.register(name=name, root=resolved, roles=roles), True


def next_command(backend: str, root: Path) -> str:
    """The exact command to run next, copy-pasteable."""
    prefix = f"{AGENT_BACKEND_ENV}={backend} "
    root_arg = "" if root.resolve() == Path.cwd().resolve() else f" --root {shlex.quote(str(root))}"
    return f'{prefix}uv run cuttlefish run "describe the change you want"{root_arg}'
