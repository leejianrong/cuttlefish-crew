"""The daemon loop that gives a project's builder its scheduled goal (CUT-245, ADR-0051).

One asyncio task owned by the fleet daemon. Every ``interval_s`` it asks the schedule store what
is due and, for each, takes the same path as "Give the builder a goal" (`give_goal`, so
worktree-per-goal and Autopilot apply). Autopilot is always on for a scheduled goal: it is what
gives the goal an end, and its plan-first step is where an unattended run stops at Needs you.

Rules, in the order they are checked:

- The manager's own project (CUT-257) is not given a goal: the schedule's text is typed into its
  session as a check-in, unless the session is busy, at a dialog or not Claude Code (skipped).
- A builder that is still on its previous goal (a session running whose Autopilot has not ended,
  or one with no Autopilot at all) is never interrupted: the run is skipped and journaled.
- A builder whose last goal has ended (done, stopped, limit, stuck) is stopped and gets the new
  goal, which is what ``give_goal`` needs to start a fresh session.
- A run missed while the daemon was down is started once on restart: the store moves
  ``next_due`` on from the time of the run, not from the missed slot.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cuttlefish.autopilot import ENDED, Limits
from cuttlefish.episodic.events import ScheduleRan, ScheduleSkipped
from cuttlefish.projects.schedules import Schedule, ScheduleStore
from cuttlefish.projects.store import Project, ProjectNotFoundError

if TYPE_CHECKING:
    from cuttlefish.fleet.daemon import FleetDaemon

_LOG = logging.getLogger(__name__)

#: How often the loop looks. A schedule is on the minute at best, so this is the lateness bound.
INTERVAL_S = 20.0


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Scheduler:
    def __init__(
        self,
        daemon: FleetDaemon,
        store: ScheduleStore,
        *,
        interval_s: float = INTERVAL_S,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._daemon = daemon
        self._store = store
        self._interval_s = interval_s
        self._clock = clock
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def close(self) -> None:
        """Stop looking. A goal already started is the builder's own and carries on."""
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # one bad pass must not end the loop
                _LOG.exception("schedule pass failed")
            await asyncio.sleep(self._interval_s)

    async def tick(self) -> None:
        """Handle every schedule that is due now, oldest first."""
        for schedule in await asyncio.to_thread(self._store.due, self._clock()):
            result = await self._run(schedule)
            await asyncio.to_thread(self._store.record_run, schedule.id, result, now=self._clock())

    async def _run(self, schedule: Schedule) -> str:
        """Start the goal or skip it; journal which, and return the result text."""
        try:
            project = self._daemon.projects.get(schedule.project_id)
        except ProjectNotFoundError:
            return "skipped: the project is no longer registered"
        reason = await self._try_start(schedule, project)
        payload: Any = ScheduleSkipped(schedule.id, reason) if reason else ScheduleRan(schedule.id)
        self._daemon.append_session_event(project, payload)
        return f"skipped: {reason}" if reason else "started"

    async def _try_start(self, schedule: Schedule, project: Project) -> str | None:
        """``None`` once the goal has started, else why it was not."""
        project_id = project.id
        if project_id == self._daemon.manager_project:
            return await self._daemon.check_in_manager(schedule.goal)
        if (await self._daemon.agent_session(project_id)).state == "running":
            phase = (await self._daemon.autopilot.status(project))["phase"]
            if phase not in ENDED:
                return f"the builder is still on its previous goal (Autopilot: {phase})"
            await self._daemon.stop_agent_session(project_id)
        try:
            await self._daemon.give_goal(project_id, schedule.goal, autopilot=Limits())
        except Exception as exc:
            _LOG.warning("scheduled goal for %s not started: %s", project.name, exc)
            return f"the goal could not be started: {exc}"
        return None
