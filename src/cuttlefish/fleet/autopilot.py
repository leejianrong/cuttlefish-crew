"""The Autopilot supervisor: keeps a project's builder going after its plan is approved (CUT-174).

A daemon-side supervisor, not a satay workflow (ADR-0044, ADR-0032): the builder is a process in
tmux that outlives the daemon, so there is nothing for a workflow to resume. What the supervisor
owns is a small state, and that lives in the episodic journal as the project's last
:class:`~cuttlefish.episodic.events.AutopilotUpdated` event (ADR-0004), read back the first time a
project is touched after a restart. The agent's ``Stop`` hook (ADR-0040) is what drives it.

It only ever sends the same two fixed messages (the approval and "continue"), through the same
guarded path as a person's message, so it types nothing into a dialog and nothing of its own.

A builder that stops because its provider's usage limit was hit (Claude Code's ``StopFailure`` hook
with ``error`` ``rate_limit``) is waited for, not nudged into the wall: Autopilot journals the
``limit_wait`` phase with the time it will continue, sends the one "continue" a minute after the
window resets, and gives up after five limited stops in a row. It does this only for a running
Autopilot (a person approved a plan), only if the project has not opted out, and a Pause, Stop or
kill switch cancels the wait (CUT-299).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cuttlefish import autopilot as rules
from cuttlefish.autopilot import (
    APPROVAL_TEXT,
    ENDED,
    NUDGE_TEXT,
    RESET_GRACE_S,
    Decision,
    Facts,
    Limits,
    reset_from_failure,
)
from cuttlefish.decision import consume as consume_ask
from cuttlefish.decision import read_ask
from cuttlefish.episodic.events import AutopilotUpdated
from cuttlefish.limits import MAX_LIMIT_BACKOFF_S, limit_backoff_from_env, max_limit_waits_from_env
from cuttlefish.plan import PLAN_PATH, Plan, read_plan
from cuttlefish.projects.store import Project
from cuttlefish.requests import RequestError
from cuttlefish.sessions.manager import goal_work_dir
from cuttlefish.sessions.transcript import UsageMeter
from cuttlefish.usage import BOARD

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


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _utc_text(epoch: float) -> str:
    """`2026-11-12 14:05 UTC`: the date too, since a weekly window resets days from now."""
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M UTC")


@dataclasses.dataclass
class _Run:
    state: AutopilotUpdated
    meter: UsageMeter = dataclasses.field(default_factory=UsageMeter)


class Autopilot:
    def __init__(
        self,
        daemon: FleetDaemon,
        *,
        settle_s: float = SETTLE_S,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._daemon = daemon
        self._settle_s = settle_s
        #: Injected so a test moves time itself and nothing waits in real time.
        self._clock = clock
        self._sleep = sleep
        #: The task waiting for a usage window to reset, per project.
        self._waits: dict[str, asyncio.Task[None]] = {}
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
        plan = await asyncio.to_thread(read_plan, goal_work_dir(project.root))
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
            "elapsed_s": self._elapsed(state, started),
            "waiting": self._waiting(state),
            "parked_s": state.parked_s,
            "resume_at": state.resume_at if state.phase == "limit_wait" else None,
            "limit_kind": state.kind or None,
            "limit_source": state.source or None,
            "continue_after_reset": project.continue_after_reset,
            "nudges": state.nudges,
            "nudges_since_progress": state.nudges_since_progress,
            "tokens_used": tokens,
            "limits": _limits(state).to_json(),
            "plan": _plan_json(plan),
            "plan_path": PLAN_PATH,
        }

    def _elapsed(self, state: AutopilotUpdated, started: float | None) -> float | None:
        """How long Autopilot has run, not counting time parked on a person (CUT-312)."""
        if started is None or state.phase not in ("running", "waiting_on_you"):
            return None
        since = _parse(state.waiting_since)
        end = since if state.phase == "waiting_on_you" and since is not None else self._clock()
        return max(0.0, end - started - state.parked_s)

    def _waiting(self, state: AutopilotUpdated) -> dict[str, Any] | None:
        """What a person is being waited for and for how long, or ``None``."""
        since = _parse(state.waiting_since)
        if state.phase not in ("awaiting_approval", "waiting_on_you") or since is None:
            return None
        return {
            "kind": state.waiting_kind,
            "since": state.waiting_since,
            "age_s": max(0.0, self._clock() - since),
        }

    async def _tokens_used(self, project: Project, run: _Run) -> int | None:
        if run.state.phase not in ("running", "paused", "done", "limit", "stuck", "waiting_on_you"):
            return None
        path = self._daemon.chat_transcript_path(project)
        if path is None:
            return None
        total = await asyncio.to_thread(run.meter.update, path)
        return max(0, total - run.state.tokens_baseline)

    # -- changing --------------------------------------------------------------------------------

    def _journal(self, project: Project, run: _Run, **changes: Any) -> None:
        if run.state.kind and "kind" not in changes and ("phase" in changes or "nudges" in changes):
            changes.update(kind="", source="")  # the limit it described is over
        state = run.state
        left = changes.get("phase", state.phase) != "waiting_on_you"
        since = _parse(state.waiting_since)
        if state.phase == "waiting_on_you" and left and since is not None:
            # Time parked on a person is not Autopilot's time (CUT-312).
            changes = {
                "parked_s": state.parked_s + max(0.0, self._clock() - since),
                "waiting_kind": "",
                "waiting_since": None,
                **changes,
            }
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
        self._cancel_wait(project.id)
        self._runs[project.id] = _Run(state)
        self._daemon.append_session_event(project, state)

    async def approve(self, project: Project) -> None:
        run = await self._run(project)
        if run is None or run.state.phase not in ("planning", "awaiting_approval"):
            raise AutopilotError("There is no plan waiting for approval.")
        plan = await asyncio.to_thread(read_plan, goal_work_dir(project.root))
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
            parked_s=0.0,
            waiting_kind="",
            waiting_since=None,
        )

    async def pause(self, project: Project) -> None:
        run = await self._run(project)
        if run is None or run.state.phase not in ("running", "limit_wait", "waiting_on_you"):
            raise AutopilotError("Autopilot is not running.")
        self._cancel(project.id)
        self._cancel_wait(project.id)
        self._journal(project, run, phase="paused", reason="Paused by you.", resume_at=None)

    async def resume(self, project: Project) -> None:
        run = await self._run(project)
        if run is None or run.state.phase not in (
            "paused",
            "stuck",
            "limit",
            "limit_wait",
            "waiting_on_you",
        ):
            raise AutopilotError("Autopilot is not paused.")
        self._cancel_wait(project.id)
        self._journal(
            project,
            run,
            phase="running",
            reason="Resumed by you.",
            resume_at=None,
            nudges_since_progress=0,
            # A limit that was reached is lifted by a person resuming: time restarts from now.
            **(
                {"approved_at": _now(), "nudges": 0, "parked_s": 0.0}
                if run.state.phase == "limit"
                else {}
            ),
        )
        self._schedule(project)

    async def stop(self, project: Project, reason: str = "Stopped by you.") -> None:
        run = await self._run(project)
        if run is None or run.state.phase in ENDED:
            return
        self._cancel(project.id)
        self._cancel_wait(project.id)
        self._journal(project, run, phase="stopped", reason=reason, resume_at=None)

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
        run = self._runs.get(project.id)
        if (
            run is not None
            and run.state.phase == "waiting_on_you"
            and not self._still_asked(project, run)
        ):
            self._journal(project, run, phase="running", reason="The builder is working again.")
        if run is not None and run.state.phase == "limit_wait":
            # The builder is working again (a person messaged it): the wait is over.
            self._cancel_wait(project.id)
            self._journal(
                project,
                run,
                phase="running",
                resume_at=None,
                reason="The builder is working again, so Autopilot stopped waiting for the limit.",
            )

    def _still_asked(self, project: Project, run: _Run) -> bool:
        """Whether what a parked Autopilot waits on is still unanswered: a decision card pending."""
        return run.state.waiting_kind == "decision" and any(
            p.record.kind == "decision" and p.record.role == "builder"
            for p in self._daemon.requests.pending(project.id)
        )

    async def on_blocked(self, project: Project, message: str) -> None:
        """The session is stopped on a terminal dialog nothing here can answer (CUT-312)."""
        run = await self._run(project)
        if run is None or run.state.phase != "running":
            return
        self._park(
            project,
            run,
            "blocked",
            "The session is waiting on a dialog in its terminal. Only the terminal can answer it.",
        )

    def _park(self, project: Project, run: _Run, kind: str, reason: str) -> None:
        """Autopilot waits on a person, and says what for and since when (CUT-312)."""
        if run.state.phase == "waiting_on_you":
            self._journal(project, run, reason=reason[:200])
            return
        self._journal(
            project,
            run,
            phase="waiting_on_you",
            waiting_kind=kind,
            waiting_since=_iso(self._clock()),
            reason=reason[:200],
        )

    # -- a usage limit ---------------------------------------------------------------------------

    async def on_stop_failure(
        self, project: Project, error: str | None, details: str | None
    ) -> None:
        """A turn ended on an API error. A usage limit on a running Autopilot is waited for."""
        if error == "rate_limit":
            await self.on_limit(project, details=details, source="hook")
        elif error == "overloaded":  # an API 529: no window, no reset time
            await self.on_backoff(project)

    async def on_board(self, project: Project, backend: str) -> None:
        """A backend's usage windows were just refreshed (Codex's ``account/rateLimits``): a
        running Autopilot on that backend whose window is out waits for it (CUT-311)."""
        run = await self._run(project)
        out = BOARD.blocked_until(backend, self._clock()) is not None
        if out and run is not None and run.state.backend == backend:
            await self.on_limit(project, source="board")

    async def on_limit(
        self, project: Project, *, details: str | None = None, source: str = "board"
    ) -> None:
        """A usage window is out. ``details`` is the failed turn's own words, ``source`` where the
        signal came from (``hook`` or ``board``); the reset is read from the words first, then
        from what the backend reported on the usage board. Never a guess: no reset, no timer."""
        run = await self._run(project)
        if run is None or run.state.phase != "running":
            return
        state = run.state
        if not project.continue_after_reset:
            self._journal(
                project,
                run,
                reason="The builder hit a usage limit. Continuing after the reset is off for "
                "this project, so it will not continue by itself.",
            )
            return
        now = self._clock()
        reset = reset_from_failure(details, now)
        if reset is None:
            reset = BOARD.blocked_until(state.backend, now)
            source = "board" if reset is not None else source
        if reset is None:
            self._journal(
                project,
                run,
                kind="window",
                source=source,
                reason="The builder hit a usage limit and the reset time is not known, so it "
                "will not continue by itself. Carry on from here when the limit has reset.",
            )
            return
        await self._enter_wait(project, run, reset + RESET_GRACE_S, "window", source)

    async def on_failure_kind(self, project: Project, failure_kind: str | None) -> None:
        """A backend's own normalised failure (``DelegationOutcome.failure_kind``): a provider
        rate limit waits for the window if the usage board names one, else backs off."""
        run = await self._run(project)
        if failure_kind != "provider_rate_limit" or run is None:
            return
        if BOARD.blocked_until(run.state.backend, self._clock()) is not None:
            await self.on_limit(project, source="board")
        else:
            await self.on_backoff(project)

    async def on_backoff(self, project: Project) -> None:
        """A provider error that names no reset (an API 429 or 529, kopicode's
        ``provider_rate_limit``): retry after a bounded, doubling delay (CUT-311). The same
        wait, the same one "continue", the same cap on waits in a row."""
        run = await self._run(project)
        if run is None or run.state.phase != "running":
            return
        if not project.continue_after_reset:
            self._journal(
                project,
                run,
                reason="The builder's provider refused a request. Continuing after a wait is "
                "off for this project, so it will not continue by itself.",
            )
            return
        delay = min(limit_backoff_from_env() * 2**run.state.limit_waits, MAX_LIMIT_BACKOFF_S)
        await self._enter_wait(project, run, self._clock() + delay, "backoff", "backoff")

    async def _enter_wait(
        self, project: Project, run: _Run, due: float, kind: str, source: str
    ) -> None:
        state = run.state
        waits = state.limit_waits + 1
        most = max_limit_waits_from_env()
        what = "a usage limit" if kind == "window" else "a provider error"
        if waits > most:
            self._journal(
                project,
                run,
                phase="limit",
                limit_waits=waits,
                reason=f"The builder stopped on {what} {most} times in a row, "
                "so Autopilot gave up. Carry on when you are ready.",
            )
            return
        started = (_parse(state.approved_at) or self._clock()) + state.parked_s
        if due - started >= state.limit_hours * 3600:
            self._journal(
                project,
                run,
                phase="limit",
                reason=f"The {'usage limit resets' if kind == 'window' else 'retry is due'} at "
                f"{_utc_text(due - RESET_GRACE_S if kind == 'window' else due)}, after "
                "Autopilot's time limit ends. Carry on when you are ready.",
            )
            return
        self._journal(
            project,
            run,
            phase="limit_wait",
            resume_at=_iso(due),
            limit_waits=waits,
            kind=kind,
            source=source,
            reason=(
                f"Usage limit reached. Will continue at {_utc_text(due)}."
                if kind == "window"
                else f"The provider refused a request. Will retry at {_utc_text(due)}."
            ),
        )
        self._arm(project.id, due)

    def _arm(self, project_id: str, due: float) -> None:
        self._cancel_wait(project_id)
        self._waits[project_id] = asyncio.ensure_future(self._continue_at(project_id, due))

    def _cancel_wait(self, project_id: str) -> None:
        task = self._waits.pop(project_id, None)
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def rearm(self, projects: Iterable[Project]) -> int:
        """After a daemon restart: a project still waiting on a usage window gets its timer back
        (one already due fires at once). Returns how many."""
        count = 0
        for project in projects:
            run = await self._run(project)
            due = _parse(run.state.resume_at) if run is not None else None
            if run is not None and run.state.phase == "limit_wait" and due is not None:
                self._arm(project.id, due)
                count += 1
        return count

    async def _continue_at(self, project_id: str, due: float) -> None:
        try:
            delay = due - self._clock()
            if delay > 0:
                await self._sleep(delay)
            run = self._runs.get(project_id)
            if run is None or run.state.phase != "limit_wait" or _parse(run.state.resume_at) != due:
                return  # stopped, paused or resumed meanwhile, or a later wait took over
            project = self._daemon.projects.get(project_id)
            if not project.continue_after_reset:
                self._journal(
                    project,
                    run,
                    phase="paused",
                    resume_at=None,
                    reason="Continuing after the reset was turned off, so Autopilot is paused.",
                )
                return
            now = self._clock()
            blocked = BOARD.blocked_until(run.state.backend, now)
            if blocked is not None and blocked + RESET_GRACE_S > now:
                # The tool still says it is out (the window moved): wait for what it names now.
                later = blocked + RESET_GRACE_S
                self._journal(
                    project,
                    run,
                    resume_at=_iso(later),
                    reason=f"Still limited. Will continue at {_utc_text(later)}.",
                )
                self._arm(project_id, later)
                return
            self._journal(
                project, run, phase="running", resume_at=None, reason="The limit has reset."
            )
            if await self._decision_pending(project, run):
                return
            await self._evaluate(project, run)  # the same limits and the same one message
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("autopilot for %s failed continuing after a limit", project_id)

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
            if run is not None and run.state.phase == "waiting_on_you":
                if self._still_asked(project, run):
                    return
                self._journal(project, run, phase="running", reason="The builder stopped again.")
            if run is None or run.state.phase not in ("planning", "running"):
                return
            if run.state.limit_waits:
                # It ran a turn after the last wait: the limits are not stopping it in a row.
                self._journal(project, run, limit_waits=0)
            if await self._decision_pending(project, run):
                return
            if run.state.phase == "planning":
                await self._plan_ready(project, run)
            else:
                await self._evaluate(project, run)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("autopilot for %s failed after a stop", project.name)

    async def _decision_pending(self, project: Project, run: _Run) -> bool:
        """The builder wrote a decision (CUT-184), or one is still waiting for the person: raise
        the card and do not nudge a builder that is waiting on an answer."""
        ask = await asyncio.to_thread(read_ask, goal_work_dir(project.root))
        if ask is not None:
            await asyncio.to_thread(consume_ask, goal_work_dir(project.root))
            try:
                self._daemon.raise_decision(
                    project.id,
                    ask.question,
                    ask.options,
                    ask.recommended,
                    role="builder",
                    cost_of_waiting=ask.cost_of_waiting,
                    wait_s=project.decision_wait_minutes * 60
                    if project.decision_wait_minutes
                    else None,
                    destructive=ask.destructive,
                )
            except RequestError as exc:
                with contextlib.suppress(Exception):
                    await self._daemon.send_chat(project.id, f"Your decision was not raised: {exc}")
                return True
            self._wait_on_decision(project, run, f"Waiting for your decision: {ask.question}")
            return True
        if any(
            p.record.kind == "decision" and p.record.role == "builder"
            for p in self._daemon.requests.pending(project.id)
        ):
            self._wait_on_decision(project, run, "Waiting for your decision.")
            return True
        return False

    def _wait_on_decision(self, project: Project, run: _Run, reason: str) -> None:
        if run.state.phase == "running":
            self._park(project, run, "decision", reason)
        else:
            self._journal(project, run, reason=reason[:200])

    async def _plan_ready(self, project: Project, run: _Run) -> None:
        plan = await asyncio.to_thread(read_plan, goal_work_dir(project.root))
        if plan is not None and plan.steps:
            self._journal(
                project,
                run,
                phase="awaiting_approval",
                waiting_kind="plan",
                waiting_since=_iso(self._clock()),
                reason=f"The plan has {len(plan.steps)} steps and is waiting for you.",
            )

    async def _baseline(self, project: Project, run: _Run) -> int:
        path = self._daemon.chat_transcript_path(project)
        return await asyncio.to_thread(run.meter.update, path) if path is not None else 0

    async def _evaluate(self, project: Project, run: _Run) -> None:
        state = run.state
        plan = await asyncio.to_thread(read_plan, goal_work_dir(project.root))
        started = _parse(state.approved_at) or self._clock()
        facts = Facts(
            steps_total=len(plan.steps) if plan else 0,
            steps_done=plan.done if plan else 0,
            steps_done_before=state.steps_done_before,
            nudges=state.nudges,
            nudges_since_progress=state.nudges_since_progress,
            elapsed_s=self._clock() - started - state.parked_s,
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
