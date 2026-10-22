"""Unit: Autopilot's policy and the token meter (CUT-174)."""

from __future__ import annotations

import json
from pathlib import Path

from cuttlefish.autopilot import STUCK_AFTER, Facts, Limits, decide
from cuttlefish.sessions.transcript import UsageMeter

LIMITS = Limits(hours=2, tokens=1000, nudges=5)


def facts(**over: float) -> Facts:
    base: dict[str, float] = {
        "steps_total": 6,
        "steps_done": 2,
        "steps_done_before": 2,
        "nudges": 1,
        "nudges_since_progress": 0,
        "elapsed_s": 60,
        "tokens_used": 10,
    }
    base.update(over)
    return Facts(**base)  # type: ignore[arg-type]


def test_steps_left_and_nothing_wrong_is_a_nudge() -> None:
    assert decide(facts(), LIMITS).action == "nudge"


def test_every_step_ticked_is_done_even_past_a_limit() -> None:
    assert decide(facts(steps_done=6, elapsed_s=10**6), LIMITS).action == "done"


def test_an_empty_checklist_is_stuck_not_done() -> None:
    result = decide(facts(steps_total=0, steps_done=0), LIMITS)
    assert result.action == "stuck" and "no checklist" in result.reason


def test_each_limit_ends_the_nudging_and_says_which() -> None:
    assert "time limit of 2 hours" in decide(facts(elapsed_s=2 * 3600), LIMITS).reason
    assert "token limit of 1,000" in decide(facts(tokens_used=1000), LIMITS).reason
    assert "limit of 5 nudges" in decide(facts(nudges=5), LIMITS).reason
    assert decide(facts(elapsed_s=2 * 3600), LIMITS).action == "limit"


def test_one_hour_is_singular() -> None:
    assert "1 hour " in decide(facts(elapsed_s=3600), Limits(hours=1)).reason


def test_stopping_again_and_again_without_a_ticked_step_is_stuck() -> None:
    assert decide(facts(nudges_since_progress=STUCK_AFTER - 1), LIMITS).action == "nudge"
    stuck = decide(facts(nudges_since_progress=STUCK_AFTER), LIMITS)
    assert stuck.action == "stuck" and "without ticking a step" in stuck.reason


def test_a_ticked_step_since_the_last_nudge_clears_the_stuck_count() -> None:
    progressed = facts(nudges_since_progress=STUCK_AFTER, steps_done=3, steps_done_before=2)
    assert decide(progressed, LIMITS).action == "nudge"


# -- the token meter ------------------------------------------------------------------------------


def _assistant(ident: str, i: int, o: int, cw: int = 0, cr: int = 0) -> str:
    usage = {
        "input_tokens": i,
        "output_tokens": o,
        "cache_creation_input_tokens": cw,
        "cache_read_input_tokens": cr,
    }
    return json.dumps({"type": "assistant", "message": {"id": ident, "usage": usage}}) + "\n"


def test_a_message_is_counted_once_however_many_blocks_repeat_its_usage(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_assistant("m1", 2, 300, 1000, 50000) * 3 + _assistant("m2", 1, 10))
    meter = UsageMeter()
    assert meter.update(path) == (2 + 300 + 1000) + (1 + 10)  # cache reads are left out


def test_the_meter_reads_only_what_was_appended(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_assistant("m1", 0, 100))
    meter = UsageMeter()
    assert meter.update(path) == 100
    with path.open("a") as handle:
        handle.write(_assistant("m2", 0, 50))
        handle.write('{"type": "assistant", "message": {"id": "m3", "usa')  # half a line
    assert meter.update(path) == 150
    with path.open("a") as handle:
        handle.write('ge": {"output_tokens": 7}}}\n')
    assert meter.update(path) == 157


def test_the_meter_starts_again_when_the_file_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_assistant("m1", 0, 1000) * 1)
    meter = UsageMeter()
    meter.update(path)
    path.write_text(_assistant("n1", 0, 5))
    assert meter.update(path) == 5
    assert UsageMeter().update(tmp_path / "missing.jsonl") == 0
