"""The Autopilot supervisor: keeps a project's builder going after its plan is approved (CUT-174).

A daemon-side supervisor, not a satay workflow (ADR-0044, ADR-0032): the builder is a process in
tmux that outlives the daemon, so there is nothing for a workflow to resume. What the supervisor
owns is a small state, and that lives in the episodic journal as the project's last
:class:`~cuttlefish.episodic.events.AutopilotUpdated` event (ADR-0004), read back the first time a
project is touched after a restart. The agent's ``Stop`` hook (ADR-0040) is what drives it.

It only ever sends the same two fixed messages (the approval and "continue"), through the same
guarded path as a person's message, so it types nothing into a dialog and nothing of its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cuttlefish import autopilot as rules
from cuttlefish.autopilot import APPROVAL_TEXT, ENDED, NUDGE_TEXT, Decision, Facts, Limits
from cuttlefish.episodic.events import AutopilotUpdated
from cuttlefish.plan import PLAN_PATH, Plan, read_plan
from cuttlefish.projects.store import Project
from cuttlefish.sessions.transcript import UsageMeter

if TYPE_CHECKING:
    from cuttlefish.fleet.daemon import FleetDaemon

_LOG = logging.getLogger(__name__)

#: How long after a ``Stop`` the supervisor waits before nudging, so a hook that arrives with a
#: new prompt right behind it (the person typing, a queued message) is seen first.
SETTLE_S = 2.0


class AutopilotError(Exception):
    """An action Autopilot cannot take now; the message says why."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclasses.dataclass
class _Run:
    state: AutopilotUpdated
    meter: UsageMeter = dataclasses.field(default_factory=UsageMeter)


class Autopilot:
    def __init__(self, daemon: FleetDaemon, *, settle_s: float = SETTLE_S) -> None:
        self._daemon = daemon
        self._settle_s = settle_s
        self._runs: dict[str, _Run] = {}
        self._loaded: set[str] = set()
        #: The settling task after a stop, per project: held so it is not collected, and
        #: cancelled when something else shows the turn is not over.
        self._tasks: dict[str, asyncio.Task[None]] = {}

    # -- reading ---------------------------------------------------------------------------------

    async def _run(self, project: Project) -> _Run | None:
        """The project's run, read from its journal the first time it is asked for."""
        if project.id not in self._loaded:
            self._loaded.add(project.id)
            events = await asyncio.to_thread(self._daemon.session_events, project)
            latest = next(
                (e.payload for e in reversed(events) if isinstance(e.payload, AutopilotUpdated)),
                None,
            )
            if latest is not None:
                self._runs[project.id] = _Run(latest)
        return self._runs.get(project.id)

    async def status(self, project: Project) -> dict[str, Any]:
        """Everything the page shows about Autopilot: its state, the plan and what it has used."""
        run = await self._run(project)
        plan = await asyncio.to_thread(read_plan, project.root)
        if run is None:
            return {"phase": "off", "plan": _plan_json(plan), "plan_path": PLAN_PATH}
        state = run.state
        tokens = await self._tokens_used(project, run)
        started = _parse(state.approved_at)
        return {
            "phase": state.phase,
            "goal": state.goal,
            "backend": state.backend,
            "reason": state.reason,
            "approved_at": state.approved_at,
            "elapsed_s": (time.time() - started) if started and state.phase == "running" else None,
            "nudges": state.nudges,
            "nudges_since_progress": state.nudges_since_progress,
            "tokens_used": tokens,
            "limits": _limits(state).to_json(),
            "plan": _plan_json(plan),
            "plan_path": PLAN_PATH,
        }

    async def _tokens_used(self, project: Project, run: _Run) -> int | None:
        if run.state.phase not in ("running", "paused", "done", "limit", "stuck"):
            return None
        path = self._daemon.chat_transcript_path(project)
        if path is None:
            return None
        total = await asyncio.to_thread(run.meter.update, path)
        return max(0, total - run.state.tokens_baseline)

    # -- changing --------------------------------------------------------------------------------

    def _journal(self, project: Project, run: _Run, **changes: Any) -> None:
        run.state = dataclasses.replace(run.state, **changes)
        self._daemon.append_session_event(project, run.state)

    async def begin(self, project: Project, goal: str, backend: str, limits: Limits) -> None:
        """A goal was given with Autopilot on: the builder is planning."""
        await self._run(project)  # load what is there so the first event continues it
        self._cancel(project.id)
        state = AutopilotUpdated(
            phase="planning",
            goal=goal,
            backend=backend,
            reason="The builder is writing its plan.",
            limit_hours=limits.hours,
            limit_tokens=limits.tokens,
            limit_nudges=limits.nudges,
        )
        self._runs[project.id] = _Run(state)
        self._daemon.append_session_event(project, state)

    async def approve(self, project: Project) -> None:
        run = await self._run(project)
        if run is None or run.state.phase not in ("planning", "awaiting_approval"):
            raise AutopilotError("There is no plan waiting for approval.")
        plan = await asyncio.to_thread(read_plan, project.root)
        if plan is None or not plan.steps:
            raise AutopilotError(
                f"The builder has not written a checklist to {PLAN_PATH} yet, "
                "so there is nothing to approve."
            )
        total = await self._baseline(project, run)
        await self._daemon.send_chat(project.id, APPROVAL_TEXT)
        self._journal(
            project,
            run,
            phase="running",
            reason="The plan was approved.",
            approved_at=_now(),
            nudges=0,
            nudges_since_progress=0,
            steps_done_before=plan.done,
            tokens_baseline=total,
        )

    async def pause(self, project: Project) -> None:
        run = await self._run(project)
        if run is None or run.state.phase != "running":
            raise AutopilotError("Autopilot is not running.")
        self._cancel(project.id)
        self._journal(project, run, phase="paused", reason="Paused by you.")

    async def resume(self, project: Project) -> None:
        run = await self._run(project)
        if run is None or run.state.phase not in ("paused", "stuck", "limit"):
            raise AutopilotError("Autopilot is not paused.")
        self._journal(
            project,
            run,
            phase="running",
            reason="Resumed by you.",
            nudges_since_progress=0,
            # A limit that was reached is lifted by a person resuming: time restarts from now.
            **({"approved_at": _now(), "nudges": 0} if run.state.phase == "limit" else {}),
        )
        self._schedule(project)

    async def stop(self, project: Project, reason: str = "Stopped by you.") -> None:
        run = await self._run(project)
        if run is None or run.state.phase in ENDED:
            return
        self._cancel(project.id)
        self._journal(project, run, phase="stopped", reason=reason)

    async def nudge_now(self, project: Project) -> None:
        """A person asked for a step now, rather than waiting for the builder to stop."""
        run = await self._run(project)
        if run is None or run.state.phase != "running":
            raise AutopilotError("Autopilot is not running.")
        await self._evaluate(project, run)

    # -- the hook --------------------------------------------------------------------------------

    def on_stop(self, project: Project) -> None:
        """The builder's turn ended (its ``Stop`` hook). Never blocks the hook that called it."""
        self._schedule(project)

    def on_activity(self, project: Project) -> None:
        """Something other than a stop happened: a pending nudge is for a turn that is not over."""
        self._cancel(project.id)

    def _schedule(self, project: Project) -> None:
        self._cancel(project.id)
        self._tasks[project.id] = asyncio.ensure_future(self._after_stop(project))

    def _cancel(self, project_id: str) -> None:
        task = self._tasks.pop(project_id, None)
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def _after_stop(self, project: Project) -> None:
        try:
            await asyncio.sleep(self._settle_s)
            run = await self._run(project)
            if run is None or run.state.phase not in ("planning", "running"):
                return
            if run.state.phase == "planning":
                await self._plan_ready(project, run)
            else:
                await self._evaluate(project, run)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("autopilot for %s failed after a stop", project.name)

    async def _plan_ready(self, project: Project, run: _Run) -> None:
        plan = await asyncio.to_thread(read_plan, project.root)
        if plan is not None and plan.steps:
            self._journal(
                project,
                run,
                phase="awaiting_approval",
                reason=f"The plan has {len(plan.steps)} steps and is waiting for you.",
            )

    async def _baseline(self, project: Project, run: _Run) -> int:
        path = self._daemon.chat_transcript_path(project)
        return await asyncio.to_thread(run.meter.update, path) if path is not None else 0

    async def _evaluate(self, project: Project, run: _Run) -> None:
        state = run.state
        plan = await asyncio.to_thread(read_plan, project.root)
        started = _parse(state.approved_at) or time.time()
        facts = Facts(
            steps_total=len(plan.steps) if plan else 0,
            steps_done=plan.done if plan else 0,
            steps_done_before=state.steps_done_before,
            nudges=state.nudges,
            nudges_since_progress=state.nudges_since_progress,
            elapsed_s=time.time() - started,
            tokens_used=await self._tokens_used(project, run) or 0,
        )
        decision = rules.decide(facts, _limits(state))
        if decision.action != "nudge":
            self._end(project, run, decision)
            return
        try:
            await self._daemon.send_chat(project.id, NUDGE_TEXT)
        except Exception as exc:  # a dialog is open, or the session is gone: the next stop retries
            _LOG.info("autopilot for %s could not nudge: %s", project.name, exc)
            return
        progressed = facts.steps_done > facts.steps_done_before
        self._journal(
            project,
            run,
            nudges=state.nudges + 1,
            nudges_since_progress=0 if progressed else state.nudges_since_progress + 1,
            steps_done_before=facts.steps_done,
            reason=f"Nudged the builder ({facts.steps_total - facts.steps_done} steps left).",
        )

    def _end(self, project: Project, run: _Run, decision: Decision) -> None:
        phase = {"done": "done", "limit": "limit", "stuck": "stuck"}[decision.action]
        self._journal(project, run, phase=phase, reason=decision.reason)


def _limits(state: AutopilotUpdated) -> Limits:
    return Limits(state.limit_hours, state.limit_tokens, state.limit_nudges)


def _plan_json(plan: Plan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {"steps": plan.to_json(), "done": plan.done, "total": len(plan.steps)}


def _parse(stamp: str | None) -> float | None:
    if not stamp:
        return None
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    return None


__all__ = ["Autopilot", "AutopilotError", "Limits"]
