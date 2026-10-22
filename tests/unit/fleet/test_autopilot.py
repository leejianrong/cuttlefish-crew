"""Unit: the Autopilot supervisor against a stand-in daemon (CUT-174)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from cuttlefish.autopilot import APPROVAL_TEXT, NUDGE_TEXT, STUCK_AFTER, Limits
from cuttlefish.episodic.events import AutopilotUpdated
from cuttlefish.fleet.autopilot import Autopilot, AutopilotError
from cuttlefish.plan import PLAN_PATH


@dataclass
class _Event:
    payload: Any


@dataclass
class _Project:
    id: str
    name: str
    root: str


class _Daemon:
    """What the supervisor asks of the daemon, kept in memory."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.events: list[_Event] = []
        self.sent: list[str] = []
        self.refuse: Exception | None = None
        self.transcript: Path | None = None

    def session_events(self, project: _Project) -> list[_Event]:
        return list(self.events)

    def append_session_event(self, project: _Project, payload: Any) -> None:
        self.events.append(_Event(payload))

    def chat_transcript_path(self, project: _Project) -> Path | None:
        return self.transcript

    async def send_chat(self, project_id: str, text: str) -> dict[str, str]:
        if self.refuse is not None:
            raise self.refuse
        self.sent.append(text)
        return {"sent": "delivered", "state": "idle"}


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.daemon = _Daemon(tmp_path)
        self.project = _Project("p1", "alpha", str(tmp_path))
        self.autopilot = Autopilot(self.daemon, settle_s=0.02)  # type: ignore[arg-type]

    def plan(self, *lines: str) -> None:
        path = self.tmp / PLAN_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Plan\n" + "\n".join(lines) + "\n")

    @property
    def state(self) -> AutopilotUpdated:
        assert isinstance(self.daemon.events[-1].payload, AutopilotUpdated)
        return self.daemon.events[-1].payload

    async def stopped(self, until: Callable[[], bool] | None = None) -> None:
        """The builder's turn ended: fire the hook and let the supervisor settle."""
        self.autopilot.on_stop(self.project)  # type: ignore[arg-type]
        for _ in range(100):
            await asyncio.sleep(0.02)
            task = self.autopilot._tasks.get(self.project.id)
            if task is None or task.done():
                break
        if until is not None:
            assert until()

    async def begin(self, limits: Limits | None = None) -> None:
        await self.autopilot.begin(self.project, "Build it", "claude-code", limits or Limits())  # type: ignore[arg-type]

    async def approved(self, limits: Limits | None = None) -> None:
        await self.begin(limits)
        self.plan("- [ ] one", "- [ ] two", "- [ ] three", "- [ ] four")
        await self.stopped()
        await self.autopilot.approve(self.project)  # type: ignore[arg-type]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


async def test_a_goal_with_autopilot_starts_in_planning_and_journals_it(rig: Rig) -> None:
    await rig.begin(Limits(hours=3, tokens=50_000, nudges=7))
    assert rig.state.phase == "planning" and rig.state.goal == "Build it"
    assert (rig.state.limit_hours, rig.state.limit_tokens, rig.state.limit_nudges) == (3, 50_000, 7)


async def test_the_plan_waits_for_approval_once_the_builder_stops_with_a_checklist(
    rig: Rig,
) -> None:
    await rig.begin()
    await rig.stopped()
    assert rig.state.phase == "planning"  # stopped, but wrote no plan
    rig.plan("- [ ] one", "- [ ] two")
    await rig.stopped()
    assert rig.state.phase == "awaiting_approval" and "2 steps" in rig.state.reason


async def test_approving_needs_a_plan_and_sends_the_fixed_approval(rig: Rig) -> None:
    await rig.begin()
    with pytest.raises(AutopilotError, match="not written a checklist"):
        await rig.autopilot.approve(rig.project)  # type: ignore[arg-type]
    rig.plan("- [x] one", "- [ ] two")
    await rig.autopilot.approve(rig.project)  # type: ignore[arg-type]
    assert rig.daemon.sent == [APPROVAL_TEXT]
    assert rig.state.phase == "running" and rig.state.approved_at is not None
    assert rig.state.steps_done_before == 1


async def test_a_refused_approval_changes_nothing(rig: Rig) -> None:
    await rig.begin()
    rig.plan("- [ ] one")
    rig.daemon.refuse = RuntimeError("a dialog is open")
    with pytest.raises(RuntimeError):
        await rig.autopilot.approve(rig.project)  # type: ignore[arg-type]
    assert rig.state.phase == "planning"


async def test_nothing_can_be_approved_when_autopilot_is_off(rig: Rig) -> None:
    with pytest.raises(AutopilotError, match="no plan waiting"):
        await rig.autopilot.approve(rig.project)  # type: ignore[arg-type]


async def test_a_stop_with_steps_left_sends_continue_and_counts_it(rig: Rig) -> None:
    await rig.approved()
    await rig.stopped()
    assert rig.daemon.sent[-1] == NUDGE_TEXT
    assert rig.state.nudges == 1 and rig.state.nudges_since_progress == 1


async def test_a_ticked_step_clears_the_no_progress_count(rig: Rig) -> None:
    await rig.approved()
    await rig.stopped()
    rig.plan("- [x] one", "- [ ] two", "- [ ] three", "- [ ] four")
    await rig.stopped()
    assert rig.state.nudges == 2 and rig.state.nudges_since_progress == 0


async def test_going_round_in_circles_ends_in_stuck_and_stops_nudging(rig: Rig) -> None:
    await rig.approved()
    for _ in range(STUCK_AFTER):
        await rig.stopped()
    assert rig.state.phase == "running" and rig.state.nudges == STUCK_AFTER
    await rig.stopped()
    assert rig.state.phase == "stuck" and "without ticking a step" in rig.state.reason
    sent = len(rig.daemon.sent)
    await rig.stopped()
    assert len(rig.daemon.sent) == sent  # nothing more once it has ended


async def test_a_finished_plan_is_done(rig: Rig) -> None:
    await rig.approved()
    rig.plan("- [x] one", "- [x] two", "- [x] three", "- [x] four")
    await rig.stopped()
    assert rig.state.phase == "done"
    assert rig.daemon.sent == [APPROVAL_TEXT]


async def test_the_nudge_limit_ends_it(rig: Rig) -> None:
    await rig.approved(Limits(nudges=2))
    for n in range(3):
        rig.plan(*[("- [x] " if i < n else "- [ ] ") + f"s{i}" for i in range(4)])
        await rig.stopped()
    assert rig.state.phase == "limit" and "2 nudges" in rig.state.reason


async def test_the_time_limit_counts_from_approval(rig: Rig) -> None:
    await rig.approved(Limits(hours=1))
    long_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    run = rig.autopilot._runs[rig.project.id]
    run.state = dataclasses.replace(run.state, approved_at=long_ago)
    await rig.stopped()
    assert rig.state.phase == "limit" and "time limit" in rig.state.reason


async def test_the_token_limit_is_what_was_used_since_approval(rig: Rig) -> None:
    transcript = rig.tmp / "t.jsonl"
    line = {"type": "assistant", "message": {"id": "m0", "usage": {"output_tokens": 900}}}
    transcript.write_text(json.dumps(line) + "\n")
    rig.daemon.transcript = transcript
    await rig.approved(Limits(tokens=10_000))
    assert rig.state.tokens_baseline == 900  # work before approval is not charged
    with transcript.open("a") as handle:
        more = {"type": "assistant", "message": {"id": "m1", "usage": {"output_tokens": 20_000}}}
        handle.write(json.dumps(more) + "\n")
    await rig.stopped()
    assert rig.state.phase == "limit" and "token limit" in rig.state.reason
    status = await rig.autopilot.status(rig.project)  # type: ignore[arg-type]
    assert status["tokens_used"] == 20_000


async def test_pause_stops_the_nudging_and_resume_carries_on(rig: Rig) -> None:
    await rig.approved()
    await rig.autopilot.pause(rig.project)  # type: ignore[arg-type]
    await rig.stopped()
    assert rig.state.phase == "paused" and rig.daemon.sent == [APPROVAL_TEXT]
    await rig.autopilot.resume(rig.project)  # type: ignore[arg-type]
    await asyncio.sleep(0.2)
    assert rig.state.phase == "running" and rig.daemon.sent[-1] == NUDGE_TEXT


async def test_stop_ends_it_and_is_idempotent(rig: Rig) -> None:
    await rig.approved()
    await rig.autopilot.stop(rig.project)  # type: ignore[arg-type]
    assert rig.state.phase == "stopped"
    count = len(rig.daemon.events)
    await rig.autopilot.stop(rig.project)  # type: ignore[arg-type]
    assert len(rig.daemon.events) == count
    await rig.stopped()
    assert rig.daemon.sent == [APPROVAL_TEXT]


async def test_a_hook_that_shows_the_turn_is_not_over_cancels_a_pending_nudge(rig: Rig) -> None:
    await rig.approved()
    rig.autopilot.on_stop(rig.project)  # type: ignore[arg-type]
    rig.autopilot.on_activity(rig.project)  # type: ignore[arg-type]  # a new prompt right behind it
    await asyncio.sleep(0.15)
    assert rig.daemon.sent == [APPROVAL_TEXT]


async def test_a_nudge_the_session_refuses_is_not_counted_and_the_next_stop_retries(
    rig: Rig,
) -> None:
    await rig.approved()
    rig.daemon.refuse = RuntimeError("a dialog is open")
    await rig.stopped()
    assert rig.state.nudges == 0
    rig.daemon.refuse = None
    await rig.stopped()
    assert rig.state.nudges == 1


async def test_a_restarted_daemon_picks_the_run_up_from_the_journal(rig: Rig) -> None:
    await rig.approved()
    await rig.stopped()
    again = Autopilot(rig.daemon, settle_s=0.02)  # type: ignore[arg-type]
    status = await again.status(rig.project)  # type: ignore[arg-type]
    assert status["phase"] == "running" and status["nudges"] == 1
    assert status["plan"]["total"] == 4
    again.on_stop(rig.project)  # type: ignore[arg-type]
    await asyncio.sleep(0.2)
    assert rig.daemon.sent[-1] == NUDGE_TEXT and rig.state.nudges == 2


async def test_status_with_no_autopilot_is_off_but_still_shows_a_plan(rig: Rig) -> None:
    status = await rig.autopilot.status(rig.project)  # type: ignore[arg-type]
    assert status["phase"] == "off" and status["plan"] is None
    rig.plan("- [ ] one")
    assert (await rig.autopilot.status(rig.project))["plan"]["total"] == 1  # type: ignore[arg-type]


async def test_resuming_after_a_limit_lifts_it_and_restarts_the_clock(rig: Rig) -> None:
    await rig.approved(Limits(nudges=1))
    await rig.stopped()
    rig.plan("- [x] one", "- [ ] two", "- [ ] three", "- [ ] four")
    await rig.stopped()
    assert rig.state.phase == "limit"
    await rig.autopilot.resume(rig.project)  # type: ignore[arg-type]
    assert rig.state.phase == "running" and rig.state.nudges == 0
    assert (
        time.time()
        - datetime.fromisoformat((rig.state.approved_at or "").replace("Z", "+00:00")).timestamp()
        < 5
    )
