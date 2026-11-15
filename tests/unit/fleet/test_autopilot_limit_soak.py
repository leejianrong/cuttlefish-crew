"""Unit (CUT-311): a "limit mid-run" soak on an injected clock. A scripted builder ticks one plan
step per turn and hits a five-hour usage window every few steps, as an unattended Auto run does.
It runs in a second: enter the wait, lose the daemon during one, resume after each reset, and end
done or cut off by the run's own hours. No model, no real time, no real limit; the payloads are
shaped like Claude Code's `StopFailure` (see test_autopilot_limit)."""

from __future__ import annotations

from typing import Any

from test_autopilot import Rig
from test_autopilot_limit import START, Clock, _board, _limited, _rig, clock

from cuttlefish.autopilot import ENDED, NUDGE_TEXT, RESET_GRACE_S, Limits
from cuttlefish.fleet.autopilot import Autopilot

__all__ = ["_board", "clock"]  # fixtures the scenario reuses

WINDOW_S = 5 * 3600
STEPS = 12


async def _run_builder(
    rig: Rig, clock: Clock, *, limit_every: int, restart_during_wait: int | None = None
) -> list[str]:
    """Drive the scripted builder until Autopilot ends. Returns each phase it entered, in order."""
    ticked = 0
    phases: list[str] = []
    waits = 0
    for _ in range(200):
        phase = rig.state.phase
        if not phases or phases[-1] != phase:
            phases.append(phase)
        if phase in ENDED:
            return phases
        if phase == "limit_wait":
            waits += 1
            if waits == restart_during_wait:
                # The daemon dies in the wait and comes back: the journal is all that is left.
                rig.autopilot._cancel_wait(rig.project.id)
                rig.autopilot = Autopilot(
                    rig.daemon,  # type: ignore[arg-type]
                    settle_s=0.02,
                    clock=clock,
                    sleep=clock.sleep,
                )
                assert await rig.autopilot.rearm([rig.project]) == 1  # type: ignore[list-item]
            await clock.release()
            continue
        ticked += 1
        rig.plan(*[f"- [{'x' if i < ticked else ' '}] step {i}" for i in range(STEPS)])
        if ticked % limit_every == 0 and ticked < STEPS:
            await _limited(rig, clock.reset_in(WINDOW_S))
        else:
            await rig.stopped()
    raise AssertionError(f"Autopilot never ended: {phases}")


async def test_a_run_that_hits_the_window_every_few_steps_finishes_across_resets(
    tmp_path: Any, clock: Clock
) -> None:
    rig = _rig(tmp_path, clock)
    await rig.approved(Limits(hours=48))
    phases = await _run_builder(rig, clock, limit_every=3, restart_during_wait=2)
    assert rig.state.phase == "done"
    assert phases.count("limit_wait") == 3 and phases[0] == "running"
    # 11 steps ticked after the approval's 0, limited after 3, 6 and 9: three five-hour windows
    # (and one grace each), and the builder was only ever sent the fixed text.
    assert clock.now - START >= 3 * (WINDOW_S + RESET_GRACE_S) - 3 * 600
    assert set(rig.daemon.sent[1:]) == {NUDGE_TEXT}
    assert rig.state.limit_waits == 0  # turns ran between the limits, so none counted against it


async def test_a_run_whose_next_window_ends_after_its_hours_stops_in_limit(
    tmp_path: Any, clock: Clock
) -> None:
    rig = _rig(tmp_path, clock)
    await rig.approved(Limits(hours=12))
    phases = await _run_builder(rig, clock, limit_every=3)
    assert phases[-1] == "limit" and "after Autopilot's time limit" in rig.state.reason
    assert rig.state.resume_at is None  # nothing is armed: a person decides
    assert clock.now - START < 12 * 3600


async def test_a_builder_that_is_limited_with_no_step_between_gives_up_after_five(
    tmp_path: Any, clock: Clock
) -> None:
    rig = _rig(tmp_path, clock)
    await rig.approved(Limits(hours=720))
    phases = await _run_builder(rig, clock, limit_every=1)
    assert phases[-1] == "limit"
    assert phases.count("limit_wait") >= 1
