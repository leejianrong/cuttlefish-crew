"""The one env-building helper every delegation backend shares (ADR-0006, ADR-0029).

Not a wire-format concern ADR-0003/ADR-0005's "no shared protocol between
backends" discipline is about — kopicode's, Claude Code's and Codex's own subprocess
invocations still speak their own native CLI language. Building the `env=`
kwarg `asyncio.create_subprocess_exec` takes is identical logic either way,
so it lives once here rather than three times.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def _own_venv() -> Path | None:
    """The virtualenv cuttlefish itself runs from, or None when it isn't in one."""
    if sys.prefix == sys.base_prefix:
        return None
    return Path(sys.prefix).resolve()


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def scrub_own_venv(environ: Mapping[str, str]) -> dict[str, str]:
    """A copy of `environ` without cuttlefish's own virtualenv (ADR-0029 decision 2.1).

    `uv run cuttlefish serve` puts cuttlefish's `.venv/bin` first on `PATH` and sets
    `VIRTUAL_ENV`; an agent inheriting that runs `python`/`pip` against cuttlefish's
    packages, not the project's (the failure that started ADR-0029). Only cuttlefish's
    *own* venv goes: a `VIRTUAL_ENV` naming some other environment is left alone.
    """
    env = dict(environ)
    own = _own_venv()
    if own is None:
        return env
    virtual_env = env.get("VIRTUAL_ENV")
    if virtual_env and Path(virtual_env).resolve() == own:
        del env["VIRTUAL_ENV"]
        env.pop("VIRTUAL_ENV_PROMPT", None)
    if "PATH" in env:
        kept = [
            entry
            for entry in env["PATH"].split(os.pathsep)
            if not (entry and _under(Path(entry).resolve(), own))
        ]
        env["PATH"] = os.pathsep.join(kept)
    return env


def merge_env(env: Mapping[str, str] | None) -> dict[str, str]:
    """The `env=` kwarg to hand `asyncio.create_subprocess_exec`.

    The daemon's `os.environ` minus cuttlefish's own venv, with `env` merged over it.
    Never `None` any more: `None` means "inherit as is", which is exactly what leaked the
    venv. An empty `env` still yields the whole (scrubbed) environment, not an empty one
    (an empty dict would leave the child no `PATH` at all).

    The full allowlisted base environment is V5-E4 (ADR-0029 decision 2.4); until then
    everything else in `os.environ` is still inherited.
    """
    return {**scrub_own_venv(os.environ), **(env or {})}
