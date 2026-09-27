"""Unit tests for classify_stream's own decision logic (cuttlefish.delegate.claude_code).

Synthetic, representative stream-json event sequences shaped exactly like what
was verified against the real `claude` binary (2026-09-20, claude 2.1.278) --
fair game to unit test, since this is cuttlefish's own reduction logic, not a
claim about what Claude Code emits in general. See that module's docstring
for the probes this shape came from.
"""

from __future__ import annotations

import pytest

from cuttlefish.agents.outcome import DelegationError, ToolCallRecord
from cuttlefish.delegate.claude_code import classify_stream


def _assistant_text(text: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _assistant_tool_use(name: str, file_path: str) -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": name, "input": {"file_path": file_path}}]
        },
    }


def _assistant_tool_call(
    call_id: str, name: str, tool_input: dict[str, object]
) -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": call_id, "name": name, "input": tool_input}]
        },
    }


def _user_tool_result(call_id: str, *, is_error: bool = False) -> dict[str, object]:
    block: dict[str, object] = {"tool_use_id": call_id, "type": "tool_result", "content": "done"}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def _result(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "result",
        "is_error": False,
        "subtype": "success",
        "result": "done",
        "permission_denials": [],
    }
    base.update(overrides)
    return base


def test_a_write_tool_use_is_a_completed_edit() -> None:
    outcome = classify_stream(
        [
            _assistant_tool_use("Write", "hello.txt"),
            _assistant_text("Created hello.txt"),
            _result(result="Created hello.txt"),
        ]
    )
    assert outcome.kind == "completed"
    assert outcome.edited_paths == ["hello.txt"]


def test_multiple_edits_are_all_recorded_without_duplicates() -> None:
    outcome = classify_stream(
        [
            _assistant_tool_use("Write", "a.py"),
            _assistant_tool_use("Edit", "b.py"),
            _assistant_tool_use("Edit", "a.py"),  # a second edit to the same file
            _result(),
        ]
    )
    assert outcome.kind == "completed"
    assert outcome.edited_paths == ["a.py", "b.py"]


def test_a_permission_denial_is_refused_even_though_is_error_is_false() -> None:
    # Verified live: a fully denied session still reports is_error=false,
    # subtype="success" -- permission_denials is the only reliable signal.
    outcome = classify_stream(
        [
            _assistant_tool_use("Write", "hello.txt"),
            _result(
                permission_denials=[
                    {
                        "tool_name": "Write",
                        "tool_use_id": "toolu_1",
                        "tool_input": {"file_path": "hello.txt", "content": "hi"},
                    }
                ]
            ),
        ]
    )
    assert outcome.kind == "refused"
    assert outcome.reason == "Write denied"


def test_a_clean_finish_with_nothing_to_do_is_completed() -> None:
    outcome = classify_stream([_assistant_text("Nothing to change here."), _result()])
    assert outcome.kind == "completed"
    assert outcome.edited_paths == []


def test_is_error_with_no_denial_is_failed() -> None:
    outcome = classify_stream(
        [_result(is_error=True, subtype="error_max_turns", result="ran out of turns")]
    )
    assert outcome.kind == "failed"
    assert outcome.reason == "ran out of turns"


def test_a_stream_with_no_result_event_raises() -> None:
    with pytest.raises(DelegationError):
        classify_stream([_assistant_text("hi")])


def test_an_absolute_edited_path_is_relativized_against_root() -> None:
    # Verified live (2026-09-20): Write/Edit tool_use blocks report an
    # *absolute* file_path, unlike kopicode's own relative edit_applied.path.
    outcome = classify_stream(
        [_assistant_tool_use("Write", "/scratch/project/hello.txt"), _result()],
        root="/scratch/project",
    )
    assert outcome.edited_paths == ["hello.txt"]


def test_an_edited_path_outside_root_is_kept_absolute_rather_than_raising() -> None:
    outcome = classify_stream(
        [_assistant_tool_use("Write", "/somewhere/else/hello.txt"), _result()],
        root="/scratch/project",
    )
    assert outcome.edited_paths == ["/somewhere/else/hello.txt"]


def test_usage_and_cost_are_read_off_the_result_event() -> None:
    # KAN-1712/ADR-0017: shaped exactly like a real `result` event captured live
    # (2026-09-27, claude 2.1.283) -- all four usage categories summed into one
    # token total, `total_cost_usd` copied through as `cost_usd`.
    outcome = classify_stream(
        [
            _result(
                usage={
                    "input_tokens": 2,
                    "output_tokens": 20,
                    "cache_creation_input_tokens": 13642,
                    "cache_read_input_tokens": 16786,
                },
                total_cost_usd=0.0581292,
            )
        ]
    )
    assert outcome.tokens == 2 + 20 + 13642 + 16786
    assert outcome.cost_usd == 0.0581292


def test_usage_and_cost_are_none_when_the_result_event_has_neither() -> None:
    outcome = classify_stream([_result()])
    assert outcome.tokens is None
    assert outcome.cost_usd is None


def test_usage_and_cost_carry_through_on_a_refused_outcome() -> None:
    outcome = classify_stream(
        [
            _result(
                permission_denials=[{"tool_name": "Write"}],
                usage={"input_tokens": 5, "output_tokens": 3},
                total_cost_usd=0.001,
            )
        ]
    )
    assert outcome.kind == "refused"
    assert outcome.tokens == 8
    assert outcome.cost_usd == 0.001


def test_usage_and_cost_carry_through_on_a_failed_outcome() -> None:
    outcome = classify_stream(
        [
            _result(
                is_error=True,
                subtype="error_max_turns",
                usage={"input_tokens": 5, "output_tokens": 3},
                total_cost_usd=0.001,
            )
        ]
    )
    assert outcome.kind == "failed"
    assert outcome.tokens == 8
    assert outcome.cost_usd == 0.001


def test_a_successful_tool_call_is_recorded_as_ok() -> None:
    # KAN-1714/ADR-0019: paired by tool_use_id, verified live as the real join
    # key both blocks carry.
    outcome = classify_stream(
        [
            _assistant_tool_call("toolu_1", "Bash", {"command": "ls"}),
            _user_tool_result("toolu_1"),
            _result(),
        ]
    )
    assert outcome.tool_calls == [
        ToolCallRecord(tool="Bash", detail='{"command": "ls"}', status="ok")
    ]


def test_a_failed_tool_call_is_recorded_as_error() -> None:
    outcome = classify_stream(
        [
            _assistant_tool_call("toolu_1", "Bash", {"command": "false"}),
            _user_tool_result("toolu_1", is_error=True),
            _result(),
        ]
    )
    assert outcome.tool_calls == [
        ToolCallRecord(tool="Bash", detail='{"command": "false"}', status="error")
    ]


def test_a_call_named_in_permission_denials_is_upgraded_to_denied() -> None:
    # The per-call is_error flag alone can't distinguish a permission denial
    # from a genuine execution failure -- only the final result event's own
    # permission_denials list can, so the upgrade happens in a second pass.
    outcome = classify_stream(
        [
            _assistant_tool_call("toolu_1", "Bash", {"command": "rm -rf /"}),
            _user_tool_result("toolu_1", is_error=True),
            _result(permission_denials=[{"tool_name": "Bash", "tool_use_id": "toolu_1"}]),
        ]
    )
    assert outcome.kind == "refused"
    assert outcome.tool_calls == [
        ToolCallRecord(tool="Bash", detail='{"command": "rm -rf /"}', status="denied")
    ]


def test_multiple_tool_calls_are_recorded_in_order() -> None:
    outcome = classify_stream(
        [
            _assistant_tool_call("toolu_1", "Bash", {"command": "echo hi"}),
            _user_tool_result("toolu_1"),
            _assistant_tool_call("toolu_2", "Write", {"file_path": "a.txt", "content": "hi"}),
            _user_tool_result("toolu_2"),
            _result(),
        ]
    )
    assert [tc.tool for tc in outcome.tool_calls] == ["Bash", "Write"]
    assert [tc.status for tc in outcome.tool_calls] == ["ok", "ok"]


def test_a_tool_result_with_no_matching_pending_call_is_ignored() -> None:
    outcome = classify_stream([_user_tool_result("toolu_unknown"), _result()])
    assert outcome.tool_calls == []
