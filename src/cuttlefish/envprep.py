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
- poetry and pipenv (V5-E6): ``poetry install`` and ``pipenv sync`` / ``pipenv install``, told to
  keep the environment in the project (``.venv``), where the agents' ``PATH`` already looks.
- Go ``go mod download``, Rust ``cargo fetch`` (``--locked`` with a ``Cargo.lock``), Ruby ``bundle
  install`` (frozen with a ``Gemfile.lock``), Java ``mvn dependency:resolve`` or Gradle's
  ``dependencies`` (the project's own wrapper when it has one). These keep their downloads outside
  the project, so there is no folder to look for: a project is prepared when cuttlefish has not
  fetched it yet or its files changed since it did.
- Anything cuttlefish cannot prepare is reported as such, with the reason, never silently skipped.

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


def state_key(ecosystem: str, path: str = ".") -> str:
    """The ``env.json`` key: the ecosystem for the project root (what it always was), and
    ``ecosystem:folder`` for one nested in a subfolder."""
    return ecosystem if path in (".", "") else f"{ecosystem}:{path}"


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
    #: Extra settings for these commands only (``POETRY_VIRTUALENVS_IN_PROJECT``...).
    env: tuple[tuple[str, str], ...] = ()
    #: The folder the commands run in, relative to the project root.
    path: str = "."

    def to_json(self) -> dict[str, Any]:
        return {
            "ecosystem": self.ecosystem,
            "path": self.path,
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
    adopt: tuple[tuple[str, str], ...] = ()
    #: ``(ecosystem, folder)`` of projects whose last install ran and made no folder: they have
    #: no dependencies, so the folder's absence is expected and the card must not call it missing.
    nothing_to_install: tuple[tuple[str, str], ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "steps": [s.to_json() for s in self.steps],
            "unsupported": [
                {"ecosystem": e, "name": ecosystem_name(e), "reason": r}
                for e, r in self.unsupported
            ],
            "nothing_to_install": [{"ecosystem": e, "path": p} for e, p in self.nothing_to_install],
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
    root = root / env.path
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


def fingerprint_now(
    root: str | Path, ecosystem: Ecosystem, *, default: str, path: str = "."
) -> str:
    """The fingerprint of `ecosystem`'s files as they are now. An install can write its own
    lockfile (``cargo fetch``, ``poetry install`` and ``uv sync`` without one do), so what to
    remember after it is what is on disk then, not what was planned: otherwise the next start
    would see a "change" nobody made and install again. `default` when it cannot be read."""
    base, folder = Path(root), path
    for env in detect(base).ecosystems:
        if env.ecosystem == ecosystem and env.path == folder:
            return fingerprint(base, env)
    return default


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
    ecosystem: str,
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


Commands = tuple[tuple[str, ...], ...]
Settings = tuple[tuple[str, str], ...]


def _python_commands(env: EcosystemEnv) -> tuple[Commands, Settings, str | None]:
    """(commands, settings, why-not). uv, poetry and pipenv are driven as themselves; a plain
    requirements project goes through uv."""
    if env.tool == "uv":
        if env.lockfile == "uv.lock":
            return (("uv", "sync", "--frozen"),), (), None
        return (("uv", "sync"),), (), None
    if env.tool == "poetry":
        # In the project, so `.venv` is where the agent's PATH looks; a lockfile that is out of
        # date makes poetry stop rather than rewrite it.
        return (
            (("poetry", "install", "--no-interaction"),),
            (("POETRY_VIRTUALENVS_IN_PROJECT", "true"),),
            None,
        )
    if env.tool == "pipenv":
        command = ("pipenv", "sync") if env.lockfile == "Pipfile.lock" else ("pipenv", "install")
        return (command,), (("PIPENV_VENV_IN_PROJECT", "1"), ("PIPENV_NOSPIN", "1")), None
    if env.tool == "pip":
        requirements = [m for m in env.manifests if m.startswith("requirements")]
        wanted = [r for r in ("requirements.txt", "requirements-dev.txt") if r in requirements]
        if not wanted:
            return (), (), "no requirements.txt to install from"
        install: list[str] = ["uv", "pip", "install"]
        for name in wanted:
            install += ["-r", name]
        # --allow-existing: a retry after a failed install finds the half-made .venv, and a bare
        # `uv venv` refuses to touch it. The packages are installed over it.
        return (("uv", "venv", "--allow-existing"), tuple(install)), (), None
    return (), (), f"{env.tool} projects are not prepared yet"


def _node_commands(root: Path, env: EcosystemEnv) -> tuple[Commands, str | None]:
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


#: Ecosystems whose downloads live outside the project (a module cache, ``~/.m2``, the gem
#: home), so there is no folder in it to look for.
_NO_FOLDER: frozenset[Ecosystem] = frozenset({"go", "rust", "java", "ruby"})


def _other_commands(root: Path, env: EcosystemEnv) -> tuple[Commands, Settings, str | None]:
    """(commands, settings, why-not) for Go, Rust, Ruby and Java."""
    if env.ecosystem == "go":
        return (("go", "mod", "download"),), (), None
    if env.ecosystem == "rust":
        fetch = ("cargo", "fetch", "--locked") if env.lockfile else ("cargo", "fetch")
        return (fetch,), (), None
    if env.ecosystem == "ruby":
        # With a Gemfile.lock, frozen: bundler stops if it is out of date instead of rewriting it.
        return (
            (("bundle", "install"),),
            ((("BUNDLE_FROZEN", "true"),) if env.lockfile else ()),
            None,
        )
    if env.ecosystem == "java":
        if env.tool == "maven":
            program = "./mvnw" if (root / "mvnw").is_file() else "mvn"
            return ((program, "-B", "-q", "dependency:resolve"),), (), None
        program = "./gradlew" if (root / "gradlew").is_file() else "gradle"
        return ((program, "--no-daemon", "--console=plain", "-q", "dependencies"),), (), None
    return (), (), f"{env.ecosystem} is not prepared yet"


def plan(root: str | Path, spec: EnvironmentSpec | None = None) -> PreparePlan:
    """The steps `root` needs now. Reads files and ``env.json`` only; runs nothing."""
    path = Path(root)
    found = spec if spec is not None else detect(path)
    state = read_state(path)
    steps: list[PrepareStep] = []
    unsupported: list[tuple[Ecosystem, str]] = []
    adopt: list[tuple[str, str]] = []
    nothing: list[tuple[str, str]] = []
    for env in found.ecosystems:
        settings: Settings = ()
        base = path / env.path
        where = "" if env.path == "." else f"in {env.path}/: "
        if env.ecosystem == "python":
            commands, settings, why_not = _python_commands(env)
        elif env.ecosystem == "node":
            commands, why_not = _node_commands(base, env)
        elif env.ecosystem in _NO_FOLDER:
            commands, settings, why_not = _other_commands(base, env)
        else:  # pragma: no cover - every detected ecosystem is handled above
            unsupported.append((env.ecosystem, "cuttlefish does not install this one yet"))
            continue
        if why_not is not None:
            unsupported.append((env.ecosystem, where + why_not))
            continue
        current = fingerprint(path, env)
        key = state_key(env.ecosystem, env.path)
        record = state.get(key, {})
        recorded = record.get("fingerprint")
        how = record.get("how")
        folder = env.env_dir or (".venv" if env.ecosystem == "python" else "node_modules")
        if env.ecosystem in _NO_FOLDER:
            # Nothing in the project to look at: what cuttlefish last fetched is all there is.
            if how == "failed" and recorded == current:
                reason = "the last install did not finish"
            elif recorded is None:
                reason = "its dependencies have not been fetched yet"
            elif recorded != current:
                changed = env.lockfile or (env.manifests[0] if env.manifests else "its files")
                reason = f"{changed} changed since the last install"
            else:
                continue
        elif how == "failed" and recorded == current:
            # Whatever is on disk came from an install that did not finish: a half-made .venv
            # looks installed, and must not hide that.
            reason = "the last install did not finish"
        elif not env.installed:
            if how == "prepared" and recorded == current and record.get("produced") is False:
                nothing.append((env.ecosystem, env.path))
                continue  # a project with no dependencies installs nothing: absence is expected
            reason = f"{folder} is missing"
        elif recorded is not None and recorded != current:
            changed = env.lockfile or (env.manifests[0] if env.manifests else "its files")
            reason = f"{changed} changed since the last install"
        else:
            if recorded is None:
                adopt.append((key, current))
            continue
        steps.append(
            PrepareStep(
                ecosystem=env.ecosystem,
                commands=commands,
                reason=reason,
                fingerprint=current,
                env=settings,
                path=env.path,
            )
        )
    return PreparePlan(
        steps=tuple(steps),
        unsupported=tuple(unsupported),
        adopt=tuple(adopt),
        nothing_to_install=tuple(nothing),
    )


def record_adopted(root: str | Path, found: PreparePlan) -> None:
    """Remember the installs a person made themselves, so a later change is noticed."""
    for key, current in found.adopt:
        write_state(root, key, fingerprint=current, how="adopted")


# --- running -------------------------------------------------------------------------------

#: Only these programs are ever started, and only by this module's own argv lists: the plan is
#: not a way to run an arbitrary command.
_ALLOWED_PROGRAMS = frozenset(
    {
        "uv", "npm", "pnpm", "yarn", "bun",
        "poetry", "pipenv", "go", "cargo", "bundle", "mvn", "gradle",
        "./mvnw", "./gradlew",  # the project's own wrappers, run from its root
    }
)  # fmt: skip


#: Keeps an install's output short and non-interactive: no progress bars, no "update
#: available" boxes, no corepack prompt (stdin is closed, so a prompt could only fail).
_QUIET_ENV = {
    "NO_COLOR": "1",
    "NO_UPDATE_NOTIFIER": "1",
    "npm_config_update_notifier": "false",
    "UV_NO_PROGRESS": "1",
    "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
}


def produced_env(root: str | Path, ecosystem: Ecosystem, path: str = ".") -> bool:
    """Whether the ecosystem's own install folder exists now (after an install). An ecosystem
    that keeps nothing in the project has no folder to be missing."""
    if ecosystem in _NO_FOLDER:
        return True
    folder = ".venv" if ecosystem == "python" else "node_modules"
    return (Path(root) / path / folder).exists()


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
    root = Path(root) / step.path
    child_env = {**merge_env(None, tools=True), **_QUIET_ENV, **dict(step.env)}
    if which is None:
        # Looked up on the PATH the child will get, which has cuttlefish's own venv removed.
        def which(program: str) -> str | None:
            if program.startswith("./"):  # the project's own wrapper
                wrapper = Path(root) / program
                return str(wrapper) if wrapper.is_file() and os.access(wrapper, os.X_OK) else None
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
    "fingerprint_now",
    "plan",
    "prepare_timeout",
    "produced_env",
    "read_state",
    "record_adopted",
    "run_step",
    "state_path",
    "write_state",
]
