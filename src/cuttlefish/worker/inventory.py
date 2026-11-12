"""What this machine has of the worker profile (CUT-214).

`probe` looks up each profile tool on `PATH` and asks it for its version, and returns the tools it
found as `{name: version}` (`""` when the tool would not say). That is the whole report: a worker
says what it has, and the controller decides what is missing (`profile.drift`). Probing starts a
program per tool, so a worker keeps the answer for a minute (`Inventory`) rather than doing it
every heartbeat.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable

from cuttlefish.worker.profile import Profile, Tool, parse_version

_TIMEOUT = 5.0
KEEP_SECONDS = 60.0

Which = Callable[[str], str | None]
Run = Callable[[list[str]], tuple[int, str]]


def _run(argv: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=_TIMEOUT, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    lines = (done.stdout or done.stderr).strip().splitlines()
    return done.returncode, lines[0] if lines else ""


def probe(profile: Profile, which: Which = shutil.which, run: Run = _run) -> dict[str, str]:
    found: dict[str, str] = {}
    for tool in profile.tools:
        version = _one(tool, which, run)
        if version is not None:
            found[tool.name] = version
    return found


def _one(tool: Tool, which: Which, run: Run) -> str | None:
    path = which(tool.command)
    if path is None:
        return None
    code, line = run([path, *tool.version_args])
    # A tool reached through a subcommand (`docker compose`) is absent when the subcommand fails;
    # a plain program that is on PATH counts even if it would not print a version.
    if code != 0 and tool.command != tool.name:
        return None
    parsed = parse_version(line)
    return ".".join(map(str, parsed)) if parsed else ""


class Inventory:
    """`probe`, kept for `KEEP_SECONDS`."""

    def __init__(
        self,
        profile: Profile,
        *,
        which: Which = shutil.which,
        run: Run = _run,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._profile, self._which, self._run, self._now = profile, which, run, now
        self._at = 0.0
        self._found: dict[str, str] | None = None

    def tools(self) -> dict[str, str]:
        if self._found is None or self._now() - self._at >= KEEP_SECONDS:
            self._found = probe(self._profile, self._which, self._run)
            self._at = self._now()
        return self._found
