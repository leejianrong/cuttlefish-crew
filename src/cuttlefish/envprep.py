"""Preparing a project's environment before its team starts (ADR-0029 decision 2, layer 3).

``plan`` turns what :mod:`cuttlefish.environment` detected into the install steps a project
needs *now*; ``run_step`` runs one. cuttlefish does this itself, before any agent turn, so a
missing ``.venv`` or ``node_modules`` costs no agent turns and shows up as its own journaled
step (``EnvironmentPrepareStarted``/``EnvironmentPrepared``).

What is installed, and when:

- Python: ``uv sync`` (``--frozen`` when there is a ``uv.lock``, so cuttlefish never rewrites a
  person's lockfile), or for a ``requirements*.txt`` project ``uv venv`` + ``uv pip install -r``.
- Node: ``npm ci`` (``npm install`` with no lockfile), ``pnpm install --frozen-lockfile``,
  ``yarn install --frozen-lockfile`` (``--immutable`` for Yarn Berry), ``bun install
  --frozen-lockfile``.
- Anything else (Go, Rust, Java, Ruby, poetry, pipenv) is reported as not prepared, with the
  reason, never silently skipped (V5-E6).

A step is needed when the project's own install is missing, or when the files that decide it
(manifest, lockfile, version hint) changed since cuttlefish last installed: the fingerprint is
kept in ``<root>/.cuttlefish/env.json`` and written only after a step succeeded. An install a
person made themselves is trusted until its files change.

Installing runs project code (``postinstall`` scripts), so whether it may happen is the
project's ``env_prepare`` setting, never an agent's decision. The commands are fixed argv lists
built here, run without a shell, from the project root, in an environment without cuttlefish's
own venv (``merge_env``), and bounded by ``CUTTLEFISH_PREPARE_TIMEOUT``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cuttlefish.delegate.subprocess_env import merge_env
from cuttlefish.environment import (
    DEFAULT_PREPARE_MODE,
    PREPARE_MODES,
    Ecosystem,
    EcosystemEnv,
    EnvironmentSpec,
    detect,
    ecosystem_name,
)

PREPARE_TIMEOUT_ENV = "CUTTLEFISH_PREPARE_TIMEOUT"
DEFAULT_PREPARE_TIMEOUT_S = 900.0

STATE_VERSION = 1
_TAIL_LINES = 40
_TAIL_CHARS = 4000
_FINGERPRINT_CAP = 5_000_000


def state_path(root: str | Path) -> Path:
    return Path(root) / ".cuttlefish" / "env.json"


@dataclass(frozen=True, slots=True)
class PrepareStep:
    """What to run for one ecosystem, and why now."""

    ecosystem: Ecosystem
    #: Commands run in order; the step stops at the first that fails.
    commands: tuple[tuple[str, ...], ...]
    reason: str
    #: Written to ``env.json`` when every command succeeded.
    fingerprint: str

    def to_json(self) -> dict[str, Any]:
        return {
            "ecosystem": self.ecosystem,
            "name": ecosystem_name(self.ecosystem),
            "commands": [list(c) for c in self.commands],
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PreparePlan:
    steps: tuple[PrepareStep, ...] = ()
    #: Ecosystems this build cannot prepare, with why: shown, never hidden.
    unsupported: tuple[tuple[Ecosystem, str], ...] = ()
    #: Ecosystems with an install the person made themselves, to remember so a later change to
    #: their files is noticed (``record_adopted``).
    adopt: tuple[tuple[Ecosystem, str], ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "steps": [s.to_json() for s in self.steps],
            "unsupported": [
                {"ecosystem": e, "name": ecosystem_name(e), "reason": r}
                for e, r in self.unsupported
            ],
        }


@dataclass(frozen=True, slots=True)
class StepResult:
    ok: bool
    exit_code: int | None
    duration_s: float
    #: The last lines of the output (stdout and stderr together), for the journal. The episodic
    #: store redacts it on write, as it does every event.
    tail: str
    command: tuple[str, ...] = ()
    cancelled: bool = False
    commands_run: int = 0
    failure: str | None = field(default=None)


# --- fingerprints and state --------------------------------------------------------------


def fingerprint(root: Path, env: EcosystemEnv) -> str:
    """A hash of everything that decides what an install should contain: the manifests and
    lockfile (bytes, capped), and the version asked for."""
    digest = hashlib.sha256()
    names = [*env.manifests, *([env.lockfile] if env.lockfile else [])]
    for name in sorted(set(names)):
        digest.update(name.encode())
        digest.update(b"\0")
        try:
            with (root / name).open("rb") as handle:
                digest.update(handle.read(_FINGERPRINT_CAP))
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    digest.update((env.version_hint or "").encode())
    return digest.hexdigest()


def read_state(root: str | Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found = data.get("ecosystems") if isinstance(data, dict) else None
    if not isinstance(found, dict):
        return {}
    return {k: v for k, v in found.items() if isinstance(k, str) and isinstance(v, dict)}


def write_state(
    root: str | Path,
    ecosystem: Ecosystem,
    *,
    fingerprint: str,
    how: str,
    produced: bool | None = None,
) -> None:
    """Remember what happened to `ecosystem`'s install at `fingerprint`: ``how`` is ``prepared``
    (we installed it), ``adopted`` (the person's own) or ``failed`` (an install of ours that did
    not finish, so whatever is on disk cannot be trusted). ``produced`` says whether a successful
    install left its folder behind: a project with no dependencies installs nothing, and that
    absence is then expected. Best effort: a read-only folder just means we cannot remember, and
    the next start decides again."""
    ecosystems = read_state(root)
    ecosystems[ecosystem] = {
        "fingerprint": fingerprint,
        "how": how,
        "at": datetime.now(UTC).isoformat(),
    }
    if produced is not None:
        ecosystems[ecosystem]["produced"] = produced
    path = state_path(root)
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"version": STATE_VERSION, "ecosystems": ecosystems}, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)


# --- planning ------------------------------------------------------------------------------

Which = Callable[[str], str | None]


def _python_commands(env: EcosystemEnv) -> tuple[tuple[tuple[str, ...], ...], str | None]:
    """(commands, why-not). uv is the one tool cuttlefish drives for Python."""
    if env.tool == "uv":
        if env.lockfile == "uv.lock":
            return (("uv", "sync", "--frozen"),), None
        return (("uv", "sync"),), None
    if env.tool == "pip":
        requirements = [m for m in env.manifests if m.startswith("requirements")]
        wanted = [r for r in ("requirements.txt", "requirements-dev.txt") if r in requirements]
        if not wanted:
            return (), "no requirements.txt to install from"
        install: list[str] = ["uv", "pip", "install"]
        for name in wanted:
            install += ["-r", name]
        # --allow-existing: a retry after a failed install finds the half-made .venv, and a bare
        # `uv venv` refuses to touch it. The packages are installed over it.
        return (("uv", "venv", "--allow-existing"), tuple(install)), None
    return (), f"{env.tool} projects are not prepared yet"


def _node_commands(root: Path, env: EcosystemEnv) -> tuple[tuple[tuple[str, ...], ...], str | None]:
    locked = env.lockfile is not None
    if env.tool == "npm":
        return (
            (
                ("npm", "ci")
                if locked and env.lockfile == "package-lock.json"
                else ("npm", "install")
            ),
        ), None
    if env.tool == "pnpm":
        return (
            (("pnpm", "install", "--frozen-lockfile") if locked else ("pnpm", "install")),
        ), None
    if env.tool == "yarn":
        immutable = (root / ".yarnrc.yml").is_file()
        flag = "--immutable" if immutable else "--frozen-lockfile"
        return ((("yarn", "install", flag) if locked else ("yarn", "install")),), None
    if env.tool == "bun":
        return ((("bun", "install", "--frozen-lockfile") if locked else ("bun", "install")),), None
    return (), f"{env.tool} projects are not prepared yet"


def plan(root: str | Path, spec: EnvironmentSpec | None = None) -> PreparePlan:
    """The steps `root` needs now. Reads files and ``env.json`` only; runs nothing."""
    path = Path(root)
    found = spec if spec is not None else detect(path)
    state = read_state(path)
    steps: list[PrepareStep] = []
    unsupported: list[tuple[Ecosystem, str]] = []
    adopt: list[tuple[Ecosystem, str]] = []
    for env in found.ecosystems:
        if env.installed is None:
            unsupported.append((env.ecosystem, "cuttlefish does not install this one yet"))
            continue
        if env.ecosystem == "python":
            commands, why_not = _python_commands(env)
        elif env.ecosystem == "node":
            commands, why_not = _node_commands(path, env)
        else:  # pragma: no cover - installed is only set for python and node
            continue
        if why_not is not None:
            unsupported.append((env.ecosystem, why_not))
            continue
        current = fingerprint(path, env)
        record = state.get(env.ecosystem, {})
        recorded = record.get("fingerprint")
        how = record.get("how")
        folder = env.env_dir or (".venv" if env.ecosystem == "python" else "node_modules")
        if how == "failed" and recorded == current:
            # Whatever is on disk came from an install that did not finish: a half-made .venv
            # looks installed, and must not hide that.
            reason = "the last install did not finish"
        elif not env.installed:
            if how == "prepared" and recorded == current and record.get("produced") is False:
                continue  # a project with no dependencies installs nothing: absence is expected
            reason = f"{folder} is missing"
        elif recorded is not None and recorded != current:
            changed = env.lockfile or (env.manifests[0] if env.manifests else "its files")
            reason = f"{changed} changed since the last install"
        else:
            if recorded is None:
                adopt.append((env.ecosystem, current))
            continue
        steps.append(
            PrepareStep(
                ecosystem=env.ecosystem, commands=commands, reason=reason, fingerprint=current
            )
        )
    return PreparePlan(steps=tuple(steps), unsupported=tuple(unsupported), adopt=tuple(adopt))


def record_adopted(root: str | Path, found: PreparePlan) -> None:
    """Remember the installs a person made themselves, so a later change is noticed."""
    for ecosystem, current in found.adopt:
        write_state(root, ecosystem, fingerprint=current, how="adopted")


# --- running -------------------------------------------------------------------------------

#: Only these programs are ever started, and only by this module's own argv lists: the plan is
#: not a way to run an arbitrary command.
_ALLOWED_PROGRAMS = frozenset({"uv", "npm", "pnpm", "yarn", "bun"})


#: Keeps an install's output short and non-interactive: no progress bars, no "update
#: available" boxes, no corepack prompt (stdin is closed, so a prompt could only fail).
_QUIET_ENV = {
    "NO_COLOR": "1",
    "NO_UPDATE_NOTIFIER": "1",
    "npm_config_update_notifier": "false",
    "UV_NO_PROGRESS": "1",
    "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
}


def produced_env(root: str | Path, ecosystem: Ecosystem) -> bool:
    """Whether the ecosystem's own install folder exists now (after an install)."""
    folder = ".venv" if ecosystem == "python" else "node_modules"
    return (Path(root) / folder).exists()


def prepare_timeout() -> float:
    raw = os.environ.get(PREPARE_TIMEOUT_ENV)
    try:
        value = float(raw) if raw else DEFAULT_PREPARE_TIMEOUT_S
    except ValueError:
        return DEFAULT_PREPARE_TIMEOUT_S
    return value if value > 0 else DEFAULT_PREPARE_TIMEOUT_S


def _tail(output: bytes) -> str:
    text = output.decode("utf-8", errors="replace")
    lines = text.splitlines()[-_TAIL_LINES:]
    return "\n".join(lines)[-_TAIL_CHARS:]


async def _kill(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(process.wait(), 5)


async def run_step(
    step: PrepareStep,
    root: str | Path,
    *,
    timeout: float | None = None,
    cancel: asyncio.Event | None = None,
    which: Which | None = None,
) -> StepResult:
    """Run `step`'s commands in order from `root`. Stops at the first failure, a timeout, or
    when `cancel` is set; the child's whole process group is killed in the last two cases."""
    limit = timeout if timeout is not None else prepare_timeout()
    child_env = {**merge_env(None), **_QUIET_ENV}
    if which is None:
        # Looked up on the PATH the child will get, which has cuttlefish's own venv removed.
        def which(program: str) -> str | None:
            return shutil.which(program, path=child_env.get("PATH"))

    started = time.monotonic()
    output = b""
    ran = 0
    last: tuple[str, ...] = ()
    for command in step.commands:
        last = command
        program = command[0]
        if program not in _ALLOWED_PROGRAMS:
            raise ValueError(f"{program!r} is not a program cuttlefish prepares with")
        if which(program) is None:
            return StepResult(
                ok=False,
                exit_code=None,
                duration_s=time.monotonic() - started,
                tail=f"{program!r} is not on PATH. Install it, or install by hand.",
                command=command,
                commands_run=ran,
                failure="tool_missing",
            )
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(root),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=child_env,
            start_new_session=True,
        )
        communicate = asyncio.ensure_future(process.communicate())
        waiters: list[asyncio.Future[Any]] = [communicate]
        cancelled = asyncio.ensure_future(cancel.wait()) if cancel is not None else None
        if cancelled is not None:
            waiters.append(cancelled)
        try:
            done, _ = await asyncio.wait(
                waiters, timeout=limit, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            await _kill(process)
            communicate.cancel()
            if cancelled is not None:
                cancelled.cancel()
            raise
        if cancelled is not None:
            cancelled.cancel()
        if communicate not in done:
            await _kill(process)
            communicate.cancel()
            was_cancel = cancelled is not None and cancelled in done
            return StepResult(
                ok=False,
                exit_code=None,
                duration_s=time.monotonic() - started,
                tail=_tail(output),
                command=command,
                cancelled=was_cancel,
                commands_run=ran,
                failure="cancelled" if was_cancel else "timeout",
            )
        stdout, _ = communicate.result()
        output += stdout or b""
        ran += 1
        if process.returncode != 0:
            return StepResult(
                ok=False,
                exit_code=process.returncode,
                duration_s=time.monotonic() - started,
                tail=_tail(output),
                command=command,
                commands_run=ran,
                failure="exit",
            )
    return StepResult(
        ok=True,
        exit_code=0,
        duration_s=time.monotonic() - started,
        tail=_tail(output),
        command=last,
        commands_run=ran,
    )


def describe_command(command: tuple[str, ...]) -> str:
    return " ".join(command)


__all__ = [
    "DEFAULT_PREPARE_MODE",
    "PREPARE_MODES",
    "PreparePlan",
    "PrepareStep",
    "StepResult",
    "describe_command",
    "fingerprint",
    "plan",
    "prepare_timeout",
    "produced_env",
    "read_state",
    "record_adopted",
    "run_step",
    "state_path",
    "write_state",
]
