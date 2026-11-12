"""Unit (CUT-299): Autopilot waits for a usage window to reset and then continues the builder, on a
fake clock so nothing waits in real time. The hard lines: only a running Autopilot, only on a
usage limit, never past its own limits, never after a Pause, Stop or the kill switch, and a project
can opt out."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from test_autopilot import Rig

from cuttlefish.autopilot import (
    MAX_LIMIT_WAITS,
    NUDGE_TEXT,
    RESET_GRACE_S,
    Limits,
    reset_from_failure,
)
from cuttlefish.fleet import autopilot
from cuttlefish.fleet.autopilot import Autopilot
from cuttlefish.usage import BOARD, Window

START = datetime(2026, 11, 12, 10, 0, tzinfo=UTC).timestamp()


class Clock:
    """Time that moves only when waits are released, and a wait that does not return until then."""

    def __init__(self) -> None:
        self.now = START
        self.waiting: list[tuple[float, asyncio.Future[None]]] = []
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self.waiting.append((seconds, future))
        await future

    def reset_in(self, seconds: float) -> str:
        """What a limited turn's error details say, relative to this clock."""
        at = int(self.now + seconds)
        return f'{{"rate_limit_info": {{"status": "rejected", "resetsAt": {at}}}}}'

    async def release(self) -> None:
        """Every wait that is waiting now returns, and time moves on by it. A wait that starts after
        this has to be released in its turn."""
        await asyncio.sleep(0.01)  # a wait just armed has not reached its sleep yet
        waiting, self.waiting = self.waiting, []
        for seconds, future in waiting:
            self.now += seconds
            future.set_result(None)
        for _ in range(10):  # the continue reads the plan in a thread: let it finish
            await asyncio.sleep(0.01)


@pytest.fixture(autouse=True)
def _board() -> Iterator[None]:
    BOARD.reset()
    yield
    BOARD.reset()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    # approved_at is stamped by the wall clock; without this the tests pass only before 10:00 UTC
    # on the day START was written.
    monkeypatch.setattr(autopilot, "_now", lambda: autopilot._iso(clock.now))
    return clock


def _rig(tmp_path: Any, clock: Clock) -> Rig:
    rig = Rig(tmp_path)
    rig.autopilot = Autopilot(rig.daemon, settle_s=0.02, clock=clock, sleep=clock.sleep)  # type: ignore[arg-type]
    return rig


async def _limited(rig: Rig, details: str | None, error: str = "rate_limit") -> None:
    await rig.autopilot.on_stop_failure(rig.project, error, details)  # type: ignore[arg-type]


async def test_a_limited_builder_is_waited_for_and_continued_once_after_the_reset(
    tmp_path: Any, clock: Clock
) -> None:
    rig = _rig(tmp_path, clock)
    await rig.approved()
    await _limited(rig, clock.reset_in(3600))
    due = START + 3600 + RESET_GRACE_S
    assert rig.state.phase == "limit_wait"
    assert rig.state.resume_at == "2026-11-12T11:01:00Z"
    assert rig.state.reason == "Usage limit reached. Will continue at 2026-11-12 11:01 UTC."
    assert rig.daemon.sent == [rig.daemon.sent[0]]  # only the approval: nothing sent into the wall
    status = await rig.autopilot.status(rig.project)  # type: ignore[arg-type]
    assert status["phase"] == "limit_wait" and status["resume_at"] == "2026-11-12T11:01:00Z"
    await clock.release()
    assert clock.slept == [due - START] and clock.now == due
    assert rig.state.phase == "running" and rig.state.resume_at is None
    assert rig.daemon.sent[-1] == NUDGE_TEXT and rig.state.nudges == 1
    assert len(rig.daemon.sent) == 2  # the approval and one "continue", not a retry loop


async def test_only_a_usage_limit_on_a_running_autopilot_is_waited_for(
    tmp_path: Any, clock: Clock
) -> None:
    rig = _rig(tmp_path, clock)
    await rig.begin()  # planning: nobody has approved a plan yet
    await _limited(rig, clock.reset_in(60))
    assert rig.state.phase == "planning"
    rig.plan("- [ ] one", "- [ ] two")
    await rig.stopped()
    await rig.autopilot.approve(rig.project)  # type: ignore[arg-type]
    for other in ("overloaded", "billing_error", "server_error", "authentication_failed", None):
        await _limited(rig, clock.reset_in(60), error=other)  # type: ignore[arg-type]
        assert rig.state.phase == "running"
    await rig.autopilot.pause(rig.project)  # type: ignore[arg-type]
    await _limited(rig, clock.reset_in(60))
    assert rig.state.phase == "paused"
    assert clock.slept == []


async def test_a_project_that_opted_out_is_never_continued(tmp_path: Any, clock: Clock) -> None:
    rig = _rig(tmp_path, clock)
    rig.project.continue_after_reset = False
    await rig.approved()
    await _limited(rig, clock.reset_in(60))
    assert rig.state.phase == "running" and "off for this project" in rig.state.reason
    await clock.release()
    assert clock.slept == [] and len(rig.daemon.sent) == 1


async def test_turning_it_off_during_a_wait_pauses_instead_of_continuing(
    tmp_path: Any, clock: Clock
) -> None:
    rig = _rig(tmp_path, clock)
    await rig.approved()
    await _limited(rig, clock.reset_in(60))
    rig.project.continue_after_reset = False
    await clock.release()
    assert rig.state.phase == "paused" and "turned off" in rig.state.reason
    assert len(rig.daemon.sent) == 1


async def test_no_reset_time_means_no_waiting_and_the_reason_says_so(
    tmp_path: Any, clock: Clock
) -> None:
    rig = _rig(tmp_path, clock)
    await rig.approved()
    await _limited(rig, "usage limit reached, try again later")
    assert rig.state.phase == "running" and "reset time is not known" in rig.state.reason
    await _limited(rig, None)
    assert clock.slept == []
    # What the tool itself reported over its own surface is a time worth waiting for.
    BOARD.report(
        "claude-code",
        {"five_hour": Window("five_hour", 100.0, int(START + 7200), None, "rejected")},
    )
    await _limited(rig, "limit")
    assert rig.state.phase == "limit_wait" and rig.state.resume_at == "2026-11-12T12:01:00Z"


async def test_a_pause_a_stop_or_the_kill_switch_cancels_the_wait(
    tmp_path: Any, clock: Clock
) -> None:
    for how in ("pause", "stop"):
        rig = _rig(tmp_path / how, clock)
        (tmp_path / how).mkdir()
        await rig.approved()
        await _limited(rig, clock.reset_in(600))
        assert rig.state.phase == "limit_wait"
        if how == "pause":
            await rig.autopilot.pause(rig.project)  # type: ignore[arg-type]
            assert rig.state.phase == "paused" and rig.state.resume_at is None
        else:
            # What the kill switch does to a builder session (`stop_agent_session`).
            await rig.autopilot.stop(rig.project, "The builder's session was stopped.")  # type: ignore[arg-type]
            assert rig.state.phase == "stopped"
        sent = len(rig.daemon.sent)
        await clock.release()
        assert len(rig.daemon.sent) == sent, how  # the released timer found nothing to do
        assert rig.state.phase in ("paused", "stopped")


async def test_carrying_on_by_hand_or_a_message_ends_the_wait(tmp_path: Any, clock: Clock) -> None:
    rig = _rig(tmp_path, clock)
    await rig.approved()
    await _limited(rig, clock.reset_in(600))
    await rig.autopilot.resume(rig.project)  # type: ignore[arg-type]
    assert rig.state.phase == "running" and rig.state.resume_at is None
    await asyncio.sleep(0.1)  # resuming has its own settle and one "continue"
    assert rig.state.nudges == 1
    await clock.release()
    assert rig.state.nudges == 1  # the timer did not send a second one

    await _limited(rig, clock.reset_in(600))
    assert rig.state.phase == "limit_wait"
    rig.autopilot.on_activity(rig.project)  # type: ignore[arg-type]  # a person messaged it
    assert rig.state.phase == "running" and "working again" in rig.state.reason
    await clock.release()
    assert rig.state.nudges == 1


async def test_it_never_continues_past_its_own_limits(tmp_path: Any, clock: Clock) -> None:
    # The nudge limit was reached while it waited: the same rule that ends nudging ends this.
    rig = _rig(tmp_path / "a", clock)
    (tmp_path / "a").mkdir()
    await rig.approved(Limits(nudges=1))
    await rig.stopped()  # the one nudge it is allowed
    sent = len(rig.daemon.sent)
    await _limited(rig, clock.reset_in(600))
    await clock.release()
    assert rig.state.phase == "limit" and "1 nudges" in rig.state.reason
    assert len(rig.daemon.sent) == sent

    # A reset after its time limit would end it: it says so now instead of waiting.
    rig = _rig(tmp_path / "b", clock)
    (tmp_path / "b").mkdir()
    await rig.approved(Limits(hours=1))
    await _limited(rig, clock.reset_in(2 * 3600))
    assert rig.state.phase == "limit" and "after Autopilot's time limit" in rig.state.reason


async def test_a_builder_waiting_on_a_decision_is_not_nudged_after_the_reset(
    tmp_path: Any, clock: Clock
) -> None:
    rig = _rig(tmp_path, clock)
    await rig.approved()
    await _limited(rig, clock.reset_in(600))
    rig.daemon.decisions.append({"role": "builder"})
    await clock.release()
    assert rig.state.phase == "running" and len(rig.daemon.sent) == 1


async def test_it_gives_up_after_five_limited_stops_in_a_row(tmp_path: Any, clock: Clock) -> None:
    rig = _rig(tmp_path, clock)
    await rig.approved(Limits(hours=720))
    for done in range(MAX_LIMIT_WAITS):
        # It ticks a step between limits, so the no-progress rule is not what ends it.
        rig.plan(*[f"- [{'x' if i < done else ' '}] step {i}" for i in range(12)])
        await _limited(rig, clock.reset_in(600))
        assert rig.state.phase == "limit_wait"
        await clock.release()
        assert rig.state.phase == "running"
    await _limited(rig, clock.reset_in(600))
    assert rig.state.phase == "limit" and "gave up" in rig.state.reason
    assert rig.state.limit_waits == MAX_LIMIT_WAITS + 1


async def test_a_turn_that_ran_after_the_wait_clears_the_count(tmp_path: Any, clock: Clock) -> None:
    rig = _rig(tmp_path, clock)
    await rig.approved(Limits(hours=720))
    for _ in range(3):
        await _limited(rig, clock.reset_in(600))
        await clock.release()
    assert rig.state.limit_waits == 3
    await rig.stopped()  # a normal Stop: the builder did a turn
    assert rig.state.limit_waits == 0


async def test_a_window_that_moved_is_waited_for_again_not_continued_into(
    tmp_path: Any, clock: Clock
) -> None:
    rig = _rig(tmp_path, clock)
    await rig.approved()
    await _limited(rig, clock.reset_in(600))
    BOARD.report(
        "claude-code", {"seven_day": Window("seven_day", 100.0, int(START + 5 * 3600), None, None)}
    )
    await clock.release()
    assert rig.state.phase == "limit_wait" and rig.state.reason.startswith("Still limited.")
    assert rig.state.resume_at == "2026-11-12T15:01:00Z"
    assert len(rig.daemon.sent) == 1
    BOARD.reset()
    await clock.release()
    assert rig.state.phase == "running" and rig.daemon.sent[-1] == NUDGE_TEXT


async def test_a_restarted_daemon_gets_the_timer_back_and_a_due_one_fires_at_once(
    tmp_path: Any, clock: Clock
) -> None:
    rig = _rig(tmp_path, clock)
    await rig.approved()
    await _limited(rig, clock.reset_in(600))
    await asyncio.sleep(0.01)  # the old process's timer is armed
    rig.autopilot._cancel_wait(rig.project.id)  # and dies with it
    again = Autopilot(rig.daemon, settle_s=0.02, clock=clock, sleep=clock.sleep)  # type: ignore[arg-type]
    clock.now += 3600  # it came back after the window had reset
    assert await again.rearm([rig.project]) == 1  # type: ignore[list-item]
    for _ in range(10):
        await asyncio.sleep(0.01)
    assert rig.state.phase == "running" and rig.daemon.sent[-1] == NUDGE_TEXT


def test_a_reset_is_read_only_from_what_the_failure_says() -> None:
    now = START
    assert reset_from_failure(None, now) is None
    assert reset_from_failure("try again later", now) is None
    assert reset_from_failure('{"resetsAt": 1794480000}', START) == 1794480000.0
    assert (
        reset_from_failure("anthropic-ratelimit-unified-reset: 1794480000", START) == 1794480000.0
    )
    assert reset_from_failure("retry-after: 120", now) == now + 120
    assert (
        reset_from_failure("resets at 2026-11-12T13:00:00Z", now)
        == datetime(2026, 11, 12, 13, 0, tzinfo=UTC).timestamp()
    )
    assert (
        reset_from_failure("2026-11-12T13:00:00+02:00", now)
        == datetime(2026, 11, 12, 11, 0, tzinfo=UTC).timestamp()
    )
    # Not in the past, not absurdly far off (a misread, not a window).
    assert reset_from_failure('{"resetsAt": 1000000000}', now) is None
    assert reset_from_failure(f"retry-after: {40 * 86400}", now) is None
    assert reset_from_failure("2026-11-12T09:00:00Z", now) is None


def test_the_continue_option_defaults_on_survives_a_reopen_and_has_a_route(tmp_path: Any) -> None:
    import satay.control
    from starlette.testclient import TestClient

    from cuttlefish.fleet.daemon import FleetDaemon
    from cuttlefish.fleet.server import TOKEN_HEADER, create_app
    from cuttlefish.fleet.tiers import Tier, required_tier
    from cuttlefish.projects.store import ProjectStore

    assert required_tier("PATCH", "/api/projects/{project_id}/continue-after-reset") is Tier.ADMIN
    store = ProjectStore.open(tmp_path / "p.db")
    project = store.register(name="a", root=str(tmp_path))
    assert project.continue_after_reset is True  # on unless a person turns it off
    app = create_app(FleetDaemon(store), security=satay.control.SecurityPolicy(token="t"))
    path = f"/api/projects/{project.id}/continue-after-reset"
    with TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: "t"}) as client:
        assert client.patch(path, json={"continue_after_reset": "no"}).status_code == 400
        off = client.patch(path, json={"continue_after_reset": False})
        assert off.status_code == 200 and off.json()["continue_after_reset"] is False
        assert (
            client.patch(
                "/api/projects/nope/continue-after-reset", json={"continue_after_reset": True}
            ).status_code
            == 404
        )
    store.close()
    assert ProjectStore.open(tmp_path / "p.db").get(project.id).continue_after_reset is False
