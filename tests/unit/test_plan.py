"""Unit: the plan checklist a builder writes (plan.py)."""

from __future__ import annotations

from pathlib import Path

from cuttlefish.plan import MAX_STEPS, PLAN_PATH, parse_plan, read_plan


def test_checklist_lines_become_steps_in_order_with_their_state() -> None:
    plan = parse_plan(
        "# Plan\n\nSome prose.\n\n- [x] Read the spec\n- [ ] Write the core\n* [X] Tests\n"
        "1. [ ] Docs\n2) [x] Release\n"
    )
    assert [(s.text, s.done) for s in plan.steps] == [
        ("Read the spec", True),
        ("Write the core", False),
        ("Tests", True),
        ("Docs", False),
        ("Release", True),
    ]
    assert (plan.done, plan.remaining) == (3, 2)


def test_lines_that_are_not_checkboxes_and_fenced_examples_are_ignored() -> None:
    plan = parse_plan("- plain bullet\n- [] no space\n```\n- [ ] in a fence\n```\n- [ ] real\n")
    assert [s.text for s in plan.steps] == ["real"]


def test_a_long_plan_is_capped_and_a_long_step_is_clipped() -> None:
    plan = parse_plan("\n".join(f"- [ ] step {n}" for n in range(MAX_STEPS + 20)))
    assert len(plan.steps) == MAX_STEPS
    assert len(parse_plan("- [ ] " + "x" * 1000).steps[0].text) == 300


def test_an_empty_plan_has_no_steps() -> None:
    plan = parse_plan("")
    assert plan.steps == () and plan.remaining == 0


def test_the_plan_is_read_from_the_artifacts_folder(tmp_path: Path) -> None:
    assert read_plan(str(tmp_path)) is None
    (tmp_path / PLAN_PATH).parent.mkdir(parents=True)
    (tmp_path / PLAN_PATH).write_text("- [ ] one\n- [x] two\n")
    plan = read_plan(str(tmp_path))
    assert plan is not None and plan.to_json() == [
        {"text": "one", "done": False},
        {"text": "two", "done": True},
    ]
