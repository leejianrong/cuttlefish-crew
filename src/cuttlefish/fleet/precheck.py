"""A schedule's precheck: a command that must exit 0 for the run to go ahead (CUT-297).

It is a command the daemon runs on its own, so it gets exactly the rules an agent's command gets
(AGENTS.md: one never-allowed list, never duplicated): the project's :class:`ConsentPolicy`, built
from its mode and command list the way the Claude Code hook builds it. The never-allowed list
holds in every mode, Auto included. A command the list does not approve is **skipped, not asked
about**: nobody is there to answer, and a precheck is the person's own words typed at the
Admin tier, not an agent's request. Ask first allows nothing.

It runs through ``/bin/sh -c`` in the project root with the allowlisted environment
(:func:`merge_env`: no daemon secrets, no cuttlefish venv), with no stdin and its output
discarded, in its own process group that is killed at the time limit
(``CUTTLEFISH_PRECHECK_TIMEOUT`` seconds, default 30). Anything but a clean exit 0 skips the run:
a precheck that cannot decide must not start unattended work.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cuttlefish.delegate.consent import ConsentPolicy
from cuttlefish.delegate.never_allowed import repo_default_branches
from cuttlefish.delegate.presets import resolve_allow
from cuttlefish.delegate.subprocess_env import merge_env

PRECHECK_TIMEOUT_ENV = "CUTTLEFISH_PRECHECK_TIMEOUT"
DEFAULT_PRECHECK_TIMEOUT_S = 30.0
_SKIPPED = "skipped by precheck: "


@dataclass(frozen=True, slots=True)
class PrecheckResult:
    """``run``: may the goal start. ``reason``: why not, for the journal and the page."""

    run: bool
    reason: str = ""


def precheck_timeout() -> float:
    try:
        value = float(os.environ.get(PRECHECK_TIMEOUT_ENV, ""))
    except ValueError:
        return DEFAULT_PRECHECK_TIMEOUT_S
    return value if value > 0 else DEFAULT_PRECHECK_TIMEOUT_S


def _skip(why: str) -> PrecheckResult:
    return PrecheckResult(False, _SKIPPED + why)


def _refusal(command: str, project: Any) -> str | None:
    """Why the project's command rules do not let `command` run unattended, or ``None``."""
    allow = [] if project.mode == "ask-first" else resolve_allow(project.allow, project.presets)
    policy = ConsentPolicy(
        allow,
        auto=project.mode == "auto",
        default_branches=repo_default_branches(Path(project.root)),
    )
    decision = policy.decide("run_shell", f"/bin/sh -c {command}")
    if decision.answer == "allow":
        return None
    if decision.askable:
        return "that command is not on this project's command list"
    return f"that command is never allowed ({decision.rule})"


async def run_precheck(
    command: str, project: Any, *, timeout_s: float | None = None
) -> PrecheckResult:
    """Run `command` for `project` (a registry ``Project``) and say whether the goal may start."""
    if getattr(project, "worker_id", None):
        return _skip("this project runs on a worker machine, where a precheck cannot run yet")
    if (why := _refusal(command, project)) is not None:
        return _skip(why)
    limit = timeout_s if timeout_s is not None else precheck_timeout()
    try:
        child = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            command,
            cwd=project.root,
            env=merge_env(None, root=project.root),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return _skip(f"it could not start ({exc})")
    try:
        code = await asyncio.wait_for(child.wait(), timeout=limit)
    except (TimeoutError, asyncio.CancelledError) as exc:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        await asyncio.shield(child.wait())
        if isinstance(exc, asyncio.CancelledError):
            raise  # the daemon is closing: nothing is left running
        return _skip(f"it did not finish in {limit:g} s")
    return PrecheckResult(True) if code == 0 else _skip(f"it exited {code}")
