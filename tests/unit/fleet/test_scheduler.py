"""The schedule loop (CUT-245): a fake clock and a fake daemon, no tmux and no tasks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cuttlefish.episodic.events import ScheduleRan, ScheduleSkipped
from cuttlefish.fleet.scheduler import Scheduler
from cuttlefish.projects.schedules import ScheduleStore
from cuttlefish.projects.store import ProjectNotFoundError

T0 = datetime(2026, 5, 1, 8, 0, tzinfo=UTC)
PROJECT = SimpleNamespace(id="p1", name="alpha")


@dataclass
class FakeDaemon:
    state: str = "none"
    phase: str = "off"
    fail: Exception | None = None
    goals: list[str] = field(default_factory=list)
    stopped: int = 0
    events: list[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.projects = SimpleNamespace(get=self._get)
        self.autopilot = SimpleNamespace(status=self._status)

    def _get(self, project_id: str) -> Any:
        if project_id != "p1":
            raise ProjectNotFoundError(project_id)
        return PROJECT

    async def _status(self, project: Any) -> dict[str, str]:
        return {"phase": self.phase}

    async def agent_session(self, project_id: str) -> Any:
        return SimpleNamespace(state=self.state)

    async def stop_agent_session(self, project_id: str) -> None:
        self.stopped += 1
        self.state = "stopped"

    async def give_goal(self, project_id: str, text: str, *, autopilot: Any) -> None:
        if self.fail:
            raise self.fail
        assert autopilot is not None  # a scheduled goal always has Autopilot's end and limits
        self.goals.append(text)
        self.state, self.phase = "running", "planning"

    manager_project: str | None = None
    check_in_result: str | None = None
    check_ins: list[str] = field(default_factory=list)

    async def check_in_manager(self, text: str) -> str | None:
        self.check_ins.append(text)
        return self.check_in_result

    def append_session_event(self, project: Any, payload: Any) -> None:
        self.events.append(payload)


class Rig:
    def __init__(self, tmp_path: Path, daemon: FakeDaemon) -> None:
        self.now = T0
        self.store = ScheduleStore.open(tmp_path / "p.db")
        self.daemon = daemon
        self.scheduler = Scheduler(daemon, self.store, clock=lambda: self.now)  # type: ignore[arg-type]
        self.schedule = self.store.add("p1", "Triage the issues", "every 1h", now=T0)

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)

    def tick(self) -> None:
        asyncio.run(self.scheduler.tick())

    def last(self) -> str:
        return self.store.get(self.schedule.id).last_result or ""


def test_nothing_happens_before_it_is_due(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeDaemon())
    rig.advance(minutes=59)
    rig.tick()
    assert rig.daemon.goals == [] and rig.daemon.events == []


def test_a_due_schedule_starts_the_goal_and_journals_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeDaemon())
    rig.advance(hours=1)
    rig.tick()
    assert rig.daemon.goals == ["Triage the issues"]
    assert rig.daemon.events == [ScheduleRan(rig.schedule.id)]
    assert rig.last() == "started"
    rig.tick()  # the same moment again does nothing
    assert rig.daemon.goals == ["Triage the issues"]


def test_a_builder_on_its_previous_goal_is_never_interrupted(tmp_path: Path) -> None:
    for phase in ("planning", "awaiting_approval", "running", "paused", "off"):
        rig = Rig(tmp_path / phase, FakeDaemon(state="running", phase=phase))
        rig.advance(hours=1)
        rig.tick()
        assert rig.daemon.goals == [] and rig.daemon.stopped == 0
        [event] = rig.daemon.events
        assert isinstance(event, ScheduleSkipped) and "previous goal" in event.reason
        assert rig.last().startswith("skipped")
        # the next try is an hour on, not a retry storm
        assert rig.store.get(rig.schedule.id).next_due == rig.now + timedelta(hours=1)


def test_a_builder_whose_goal_ended_is_restarted_with_the_new_one(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeDaemon(state="running", phase="done"))
    rig.advance(hours=1)
    rig.tick()
    assert rig.daemon.stopped == 1 and rig.daemon.goals == ["Triage the issues"]


def test_a_start_that_fails_is_a_journaled_skip(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeDaemon(fail=RuntimeError("no backend on PATH")))
    rig.advance(hours=1)
    rig.tick()
    [event] = rig.daemon.events
    assert isinstance(event, ScheduleSkipped) and "no backend on PATH" in event.reason


def test_downtime_runs_a_missed_schedule_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeDaemon())
    rig.advance(hours=30)  # thirty missed slots
    rig.tick()
    rig.tick()
    assert rig.daemon.goals == ["Triage the issues"]


def test_a_removed_project_and_a_disabled_schedule(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeDaemon())
    gone = rig.store.add("gone", "x", "every 1h", now=T0)
    off = rig.store.add("p1", "y", "every 1h", now=T0)
    rig.store.set_enabled(off.id, False, now=T0)
    rig.advance(hours=1)
    rig.tick()
    assert rig.store.get(gone.id).last_result == "skipped: the project is no longer registered"
    assert rig.store.get(off.id).last_run is None
    assert rig.daemon.goals == ["Triage the issues"]


def test_the_loop_runs_and_cancels_cleanly(tmp_path: Path) -> None:
    async def scenario() -> None:
        rig = Rig(tmp_path, FakeDaemon())
        rig.scheduler._interval_s = 0.01
        rig.advance(hours=1)
        rig.scheduler.start()
        for _ in range(200):
            if rig.daemon.goals:
                break
            await asyncio.sleep(0.01)
        await rig.scheduler.close()
        assert rig.daemon.goals == ["Triage the issues"]
        assert rig.scheduler._task is None

    asyncio.run(scenario())


def test_a_schedule_on_the_manager_types_a_check_in_and_never_a_goal(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeDaemon(manager_project="p1", state="running", phase="running"))
    rig.advance(hours=1)
    rig.tick()
    assert rig.daemon.check_ins == ["Triage the issues"] and rig.daemon.goals == []
    assert rig.daemon.events == [ScheduleRan(rig.schedule.id)] and rig.last() == "started"


def test_a_busy_manager_skips_the_check_in_and_journals_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path, FakeDaemon(manager_project="p1", check_in_result="the manager is busy"))
    rig.advance(hours=1)
    rig.tick()
    [event] = rig.daemon.events
    assert isinstance(event, ScheduleSkipped) and event.reason == "the manager is busy"
    assert rig.last() == "skipped: the manager is busy"
