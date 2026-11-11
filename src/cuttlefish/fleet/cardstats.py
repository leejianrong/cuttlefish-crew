"""The diff stat and pull request state a project card shows (CUT-287), kept by a background sweep.

The project list is polled every few seconds for every project, so nothing in it may run git or
``gh``. This holds the answers instead: one worker thread walks the projects once a minute, and for
each one whose last goal has a branch (and that is not placed on a worker) reads the branch's counts
with the same cheap numstat read the status strip uses. A project whose goal has a pull request
also gets its state and checks, read with ``gh`` far less often (five minutes, an hour once merged
or closed). The list only ever calls :meth:`CardStats.get`, a dictionary lookup, and :meth:`touch`,
which starts a sweep when one is due and never waits for it. The first list after a start has no
stats; the next poll does. Each entry is small and left out when there is nothing to show: the keys
is a short positional list because this rides on every card of a 100-project list that has a byte
budget (``test_project_list_budget.py``): ``[files, added, removed, uncommitted (0 or 1), pull
request, checks]``, the pull request ``"o"`` open, ``"m"`` merged or ``"c"`` closed, its checks
``"f"`` failing, ``"p"`` passing or ``"r"`` running, and trailing items left off when empty.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from cuttlefish.fleet.goaldiff import goal_stat
from cuttlefish.fleet.prstatus import read_pr_status
from cuttlefish.projects.store import Project
from cuttlefish.sessions.manager import read_goal

_LOG = logging.getLogger(__name__)
_PR = {"open": "o", "merged": "m", "closed": "c"}
_CHECKS = {"failing": "f", "passing": "p", "pending": "r"}
SWEEP_EVERY_S = 60.0
PR_EVERY_S = 300.0
PR_FINAL_EVERY_S = 3600.0


class CardStats:
    def __init__(
        self,
        projects: Callable[[], Sequence[Project]],
        goal_of: Callable[[Project], dict[str, str] | None],
    ) -> None:
        self._projects = projects
        #: The goal with its ``pr_url`` (a journal read), only asked for a project with a branch.
        self._goal_of = goal_of
        self._entries: dict[str, list[Any]] = {}
        #: `project id -> (when, state, checks)` of the last gh read.
        self._prs: dict[str, tuple[float, str, str]] = {}
        self._last = -SWEEP_EVERY_S
        self._lock = threading.Lock()
        self._busy = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="card-stats")

    def get(self, project_id: str) -> list[Any] | None:
        return self._entries.get(project_id)

    def touch(self) -> None:
        """Start a sweep if one is due and none is running. Never waits for it."""
        now = time.monotonic()
        with self._lock:
            if self._busy or now - self._last < SWEEP_EVERY_S:
                return
            self._busy = True
            self._last = now
        try:
            self._pool.submit(self._sweep)
        except RuntimeError:  # closed
            self._busy = False

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _sweep(self) -> None:
        try:
            seen: set[str] = set()
            for project in list(self._projects()):
                try:
                    entry = self._one(project)
                except Exception:  # one odd project must not stop the rest
                    _LOG.exception("card stat for %s failed", project.id)
                    entry = None
                if entry is None:
                    self._entries.pop(project.id, None)
                else:
                    self._entries[project.id] = entry
                    seen.add(project.id)
            for gone in set(self._entries) - seen:
                del self._entries[gone]
        finally:
            self._busy = False

    def _one(self, project: Project) -> list[Any] | None:
        if project.worker_id is not None:
            return None
        goal = read_goal(project.root)
        if not goal or not goal.get("branch"):
            return None
        stat = goal_stat(project.root, goal)
        if not stat.ok:
            return None
        pr = checks = ""
        full = self._goal_of(project)
        if full and full.get("pr_url"):
            state, state_checks = self._pr(project, full)
            pr, checks = _PR.get(state, ""), _CHECKS.get(state_checks, "")
        if not stat.files_changed and not pr:
            return None
        entry: list[Any] = [
            stat.files_changed, stat.additions, stat.deletions, int(bool(stat.dirty)), pr, checks
        ]  # fmt: skip
        while entry[-1] in ("", 0) and len(entry) > 3:
            entry.pop()
        return entry

    def _pr(self, project: Project, goal: dict[str, str]) -> tuple[str, str]:
        now = time.monotonic()
        held = self._prs.get(project.id)
        if held is not None:
            every = PR_FINAL_EVERY_S if held[1] in ("merged", "closed") else PR_EVERY_S
            if now - held[0] < every:
                return held[1], held[2]
        status = read_pr_status(project.root, goal)
        if not status.ok or not status.state:
            # Keep what was known (a flaky gh must not blank a card), try again next time.
            self._prs[project.id] = (now, held[1] if held else "", held[2] if held else "")
            return (held[1], held[2]) if held else ("", "")
        self._prs[project.id] = (now, status.state, status.checks_state or "")
        return status.state, status.checks_state or ""
