"""Unit tests for classify_stream's own decision logic (cuttlefish.delegate.codex).

Synthetic, representative `--json` event sequences shaped exactly like what
was verified against the real `codex` binary (2026-09-28, codex-cli 0.155.1)
-- fair game to unit test, since this is cuttlefish's own reduction logic, not
a claim about what Codex emits in general. See that module's docstring for
the probes this shape came from.
"""

from __future__ import annotations

import pytest

from cuttlefish.agents.outcome import DelegationError, ToolCallRecord
from cuttlefish.delegate.codex import classify_stream


def _turn_completed(**usage_overrides: object) -> dict[str, object]:
    usage: dict[str, object] = {
        "input_tokens": 100,
        "cached_input_tokens": 50,
        "cache_write_input_tokens": 0,
        "output_tokens": 20,
        "reasoning_output_tokens": 0,
    }
    usage.update(usage_overrides)
    return {"type": "turn.completed", "usage": usage}


def _file_change(path: str, kind: str = "add") -> dict[str, object]:
    return {
        "type": "item.completed",
        "item": {"id": "item_1", "type": "file_change", "changes": [{"path": path, "kind": kind}]},
    }


def _command_execution(command: str, *, exit_code: int = 0) -> dict[str, object]:
    return {
        "type": "item.completed",
        "item": {
            "id": "item_2",
            "type": "command_execution",
            "command": command,
            "aggregated_output": "",
            "exit_code": exit_code,
            "status": "completed",
        },
    }


def test_a_file_change_item_is_a_completed_edit() -> None:
    outcome = classify_stream([_file_change("/scratch/hello.txt"), _turn_completed()])
    assert outcome.kind == "completed"
    assert outcome.edited_paths == ["/scratch/hello.txt"]


def test_multiple_file_changes_are_all_recorded_without_duplicates() -> None:
    outcome = classify_stream(
        [
            _file_change("/scratch/a.py"),
            _file_change("/scratch/b.py"),
            _file_change("/scratch/a.py"),
            _turn_completed(),
        ]
    )
    assert outcome.edited_paths == ["/scratch/a.py", "/scratch/b.py"]


def test_one_file_change_item_touching_several_files_records_every_path() -> None:
    # Verified live (2026-09-29): a single `file_change` item can carry several
    # `changes` -- one patch editing a module and its test -- and every one of
    # them is an edit, not just the first.
    multi_file_item: dict[str, object] = {
        "type": "item.completed",
        "item": {
            "id": "item_3",
            "type": "file_change",
            "changes": [
                {"path": "/scratch/test_a.py", "kind": "update"},
                {"path": "/scratch/a.py", "kind": "update"},
            ],
        },
    }
    outcome = classify_stream([multi_file_item, _turn_completed()], root="/scratch")
    assert outcome.edited_paths == ["test_a.py", "a.py"]
    assert outcome.summary == "Codex edited 2 file(s)"


def test_an_absolute_edited_path_is_relativized_against_root() -> None:
    outcome = classify_stream(
        [_file_change("/scratch/project/hello.txt"), _turn_completed()],
        root="/scratch/project",
    )
    assert outcome.edited_paths == ["hello.txt"]


def test_an_edited_path_outside_root_is_kept_absolute_rather_than_raising() -> None:
    outcome = classify_stream(
        [_file_change("/somewhere/else/hello.txt"), _turn_completed()],
        root="/scratch/project",
    )
    assert outcome.edited_paths == ["/somewhere/else/hello.txt"]


def test_a_clean_finish_with_nothing_to_do_is_completed() -> None:
    outcome = classify_stream([_turn_completed()])
    assert outcome.kind == "completed"
    assert outcome.edited_paths == []


def test_turn_failed_is_a_failed_outcome() -> None:
    outcome = classify_stream(
        [{"type": "turn.failed", "error": {"message": "invalid_request_error: bad model"}}]
    )
    assert outcome.kind == "failed"
    assert outcome.reason == "invalid_request_error: bad model"


def test_a_stream_with_neither_turn_completed_nor_turn_failed_raises() -> None:
    with pytest.raises(DelegationError):
        classify_stream([{"type": "thread.started", "thread_id": "abc"}])


def test_no_edit_and_no_rejection_marker_in_stderr_reads_as_completed() -> None:
    # Codex's own item stream carries no structured "refused" signal at all
    # (verified live) -- absent any stderr hint, "no edit landed" must default
    # to "nothing to do," the same precedent kopicode/Claude Code both hold.
    outcome = classify_stream([_turn_completed()], stderr_tail="some unrelated warning")
    assert outcome.kind == "completed"


def test_no_edit_with_a_rejection_marker_in_stderr_is_refused() -> None:
    stderr = (
        "ERROR codex_core::tools::router: error=patch rejected: writing is blocked by "
        "read-only sandbox; rejected by user approval settings"
    )
    outcome = classify_stream([_turn_completed()], stderr_tail=stderr)
    assert outcome.kind == "refused"
    assert outcome.reason == stderr


def test_tokens_sum_input_and_output_only_not_cache_or_reasoning_subfields() -> None:
    # KAN-1712/ADR-0017's own tokens field -- verified live that
    # cached_input_tokens/reasoning_output_tokens read as a subset/breakdown of
    # input_tokens/output_tokens, not an additional pool, so summing them too
    # would overcount.
    outcome = classify_stream(
        [
            _turn_completed(
                input_tokens=15578,
                cached_input_tokens=11136,
                cache_write_input_tokens=0,
                output_tokens=8,
                reasoning_output_tokens=0,
            )
        ]
    )
    assert outcome.tokens == 15578 + 8


def test_tokens_is_none_when_usage_is_entirely_absent() -> None:
    outcome = classify_stream([{"type": "turn.completed"}])
    assert outcome.tokens is None


def test_cost_usd_is_always_none() -> None:
    # Codex reports no dollar figure at all, verified live -- unlike Claude
    # Code, this is never populated for this backend.
    outcome = classify_stream([_file_change("/scratch/hello.txt"), _turn_completed()])
    assert outcome.cost_usd is None


def test_a_file_change_item_is_recorded_as_an_ok_tool_call() -> None:
    # KAN-1714/ADR-0019: verified live that a rejected patch never produces a
    # file_change item at all, so every one observed is a landed edit.
    outcome = classify_stream([_file_change("/scratch/hello.txt"), _turn_completed()])
    assert outcome.tool_calls == [
        ToolCallRecord(tool="file_change", detail="add /scratch/hello.txt", status="ok")
    ]


def test_a_successful_command_execution_is_recorded_as_ok() -> None:
    outcome = classify_stream([_command_execution("ls -la"), _turn_completed()])
    assert outcome.tool_calls == [
        ToolCallRecord(tool="command_execution", detail="ls -la", status="ok")
    ]


def test_a_failed_command_execution_is_recorded_as_error() -> None:
    outcome = classify_stream([_command_execution("false", exit_code=1), _turn_completed()])
    assert outcome.tool_calls == [
        ToolCallRecord(tool="command_execution", detail="false", status="error")
    ]


def test_multiple_tool_calls_are_recorded_in_order() -> None:
    outcome = classify_stream(
        [_file_change("/scratch/a.txt"), _command_execution("ls"), _turn_completed()]
    )
    assert [tc.tool for tc in outcome.tool_calls] == ["file_change", "command_execution"]


def test_an_agent_message_item_is_not_recorded_as_a_tool_call() -> None:
    outcome = classify_stream(
        [
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "hi"},
            },
            _turn_completed(),
        ]
    )
    assert outcome.tool_calls == []
