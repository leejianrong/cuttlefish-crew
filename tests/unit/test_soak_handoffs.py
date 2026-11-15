"""The soak's per-handoff rows (CUT-313): synthetic samples, no daemon, no model."""

from __future__ import annotations

from typing import Any

from cuttlefish import soak, soak_run
from cuttlefish.soak import Sample

ROW = {
    "at": "2026-10-22T10:10:00Z",
    "repeated_commands": 2,
    "repeated_edits": 0,
    "plan_ticked": True,
    "recovery_s": 240,
    "recovery_ratio": 0.2,
    "nudges": 1,
}


def _samples(rows: tuple[dict[str, Any], ...]) -> list[Sample]:
    return [
        Sample(t=0, phase="running", tokens=0),
        Sample(t=600, phase="done", tokens=5, refreshes=len(rows), handoff_rows=rows),
    ]


def test_the_score_carries_one_row_per_handoff() -> None:
    result = soak.score(_samples((ROW, {**ROW, "at": "2026-10-22T11:00:00Z"})))
    assert len(result.handoffs) == 2
    assert result.to_json()["handoffs"][0]["repeated_commands"] == 2


def test_the_report_has_a_table_that_says_it_is_not_a_score() -> None:
    text = soak.render_report(soak.score(_samples((ROW,))))
    assert "| Handoffs (context refreshes) | 1 |" in text
    assert "Advisory checks, not a quality score" in text
    assert "| 2026-10-22T10:10:00Z | 2 | 0 | yes | 4 min (0.2x the usual gap) | 1 |" in text


def test_a_run_without_handoffs_has_no_table() -> None:
    assert "## Handoffs" not in soak.render_report(soak.score(_samples(())))


def test_a_row_without_a_move_or_plan_says_so() -> None:
    bare = {"at": "t", "plan_ticked": None, "recovery_s": None}
    text = soak.render_report(soak.score(_samples((bare,))))
    assert "| t | 0 | 0 | n/a | no move yet | 0 |" in text


def test_rows_survive_a_saved_run() -> None:
    samples = _samples((ROW,))
    loaded, _ = soak.load_run(soak.dump_run(samples, []))
    assert loaded == samples


def test_the_driver_keeps_the_latest_measures_of_each_refresh() -> None:
    reads = iter([{**ROW, "repeated_commands": 0}, ROW])

    class Api:
        def get(self, path: str) -> dict[str, Any]:
            if path.endswith("/autopilot"):
                return {"phase": "running", "plan": {}}
            if path.endswith("/requests"):
                return {"pending": []}
            event = {"at": ROW["at"], "kind": "refresh", "text": "x", "handoff": next(reads)}
            return {"timeline": [event]}

    plan = soak_run.RunPlan(
        project_id="p", goal="g", backend="claude-code", hours=1, tokens=1, nudges=1
    )
    seen: set[tuple[str, str, str]] = set()
    kwargs: dict[str, Any] = {"slow": True, "started_iso": "2026-01-01T00:00:00Z", "seen": seen}
    first, _ = soak_run.read_sample(Api(), plan, 0, carried=None, **kwargs)  # type: ignore[arg-type]
    second, _ = soak_run.read_sample(Api(), plan, 1, carried=first, **kwargs)  # type: ignore[arg-type]
    assert len(first.handoff_rows) == 1 and first.handoff_rows[0]["repeated_commands"] == 0
    assert len(second.handoff_rows) == 1 and second.handoff_rows[0]["repeated_commands"] == 2
