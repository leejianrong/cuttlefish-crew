"""Unit: advisory post-boundary measures on a context refresh (CUT-313)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cuttlefish.sessions.transcript import items_for
from cuttlefish.timeline import transcript_events


def _stamp(minute: int) -> datetime:
    return datetime(2026, 10, 22, 10, minute, tzinfo=UTC)


def _entry(minute: int, **fields: Any) -> dict[str, Any]:
    return {"timestamp": f"2026-10-22T10:{minute:02d}:00Z", "uuid": f"u{minute}", **fields}


def _call(minute: int, name: str, tool_input: dict[str, str]) -> dict[str, Any]:
    block = {"type": "tool_use", "id": f"t{minute}", "name": name, "input": tool_input}
    return _entry(minute, type="assistant", message={"content": [block]})


def test_a_native_compaction_gets_its_post_boundary_measures() -> None:
    entries = [
        _call(1, "Bash", {"command": "uv run pytest -q"}),
        _call(2, "Edit", {"file_path": "src/a.py", "new_string": "x"}),
        _entry(10, type="system", subtype="compact_boundary"),
        _call(11, "Bash", {"command": "uv run pytest -q"}),  # repeated after the boundary
        _call(12, "Edit", {"file_path": "src/a.py", "new_string": "x"}),  # a repeated edit
        _call(13, "Bash", {"command": "ls"}),
        _call(14, "Edit", {"file_path": ".cuttlefish/artifacts/plan.md", "new_string": "[x]"}),
    ]
    items = [item for entry in entries for item in items_for(entry)]
    commits = [_stamp(m) for m in (0, 20, 40)]
    nudges = [_stamp(12), _stamp(50)]

    (refresh,) = [e for e in transcript_events(items, commits, nudges) if e.kind == "refresh"]

    assert refresh.handoff == {
        "repeated_commands": 1,
        "repeated_edits": 1,
        "plan_ticked": True,
        "recovery_s": 240,  # the plan edit at 10:14, ahead of the 10:20 commit
        "recovery_ratio": 0.2,  # against a 20 minute median gap
        "nudges": 1,
    }


def test_nothing_after_the_boundary_is_not_guessed_at() -> None:
    items = items_for(_entry(10, type="system", subtype="compact_boundary"))

    (refresh,) = transcript_events(items)

    assert refresh.handoff == {
        "repeated_commands": 0,
        "repeated_edits": 0,
        "plan_ticked": None,
        "recovery_s": None,
        "recovery_ratio": None,
        "nudges": 0,
    }
