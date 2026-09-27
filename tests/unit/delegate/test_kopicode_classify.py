"""Unit tests for classify_stream's own decision logic.

Synthetic, representative `run --print` event sequences — fair game to unit test,
since this is cuttlefish's own reduction logic, not a claim about what kopicode
actually emits. The real stream shape is exercised against the real binary in
tests/integration/delegate/test_kopicode_real.py (docs/PLAN.md "Testing approach").
"""

from __future__ import annotations

import pytest

from cuttlefish.agents.outcome import ToolCallRecord
from cuttlefish.delegate.kopicode import DelegationError, classify_stream


def test_an_edit_landing_is_completed() -> None:
    outcome = classify_stream(
        [
            {"kind": "edit_applied", "path": ".gitignore"},
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert outcome.kind == "completed"
    assert outcome.edited_paths == [".gitignore"]


def test_multiple_edits_are_all_recorded() -> None:
    outcome = classify_stream(
        [
            {"kind": "edit_applied", "path": "a.py"},
            {"kind": "edit_applied", "path": "b.py"},
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert outcome.kind == "completed"
    assert outcome.edited_paths == ["a.py", "b.py"]


def test_a_denied_action_with_no_edit_is_refused() -> None:
    outcome = classify_stream(
        [
            {
                "kind": "permission_decided",
                "decision": "deny",
                "source": "policy",
                "reason": "run_shell is not on the declared allowlist",
            },
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert outcome.kind == "refused"
    assert outcome.reason == "run_shell is not on the declared allowlist"


def test_a_denial_does_not_override_an_edit_that_did_land() -> None:
    # e.g. a shell command was denied but the model still wrote the file directly.
    outcome = classify_stream(
        [
            {"kind": "permission_decided", "decision": "deny", "reason": "no shell"},
            {"kind": "edit_applied", "path": ".gitignore"},
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert outcome.kind == "completed"
    assert outcome.edited_paths == [".gitignore"]


def test_a_clean_finish_with_nothing_to_do_is_completed() -> None:
    # A read-only / informational task: the model just answered in prose.
    outcome = classify_stream([{"kind": "session_ended", "reason": "completed", "exit_code": 0}])
    assert outcome.kind == "completed"
    assert outcome.edited_paths == []


def test_a_nonzero_exit_with_no_denial_is_failed() -> None:
    outcome = classify_stream([{"kind": "session_ended", "reason": "max_turns", "exit_code": 4}])
    assert outcome.kind == "failed"
    assert outcome.reason == "exit_code=4 reason=max_turns"


def test_a_failed_session_folds_in_a_stderr_tail() -> None:
    # e.g. a provider's own 401 only ever shows up on stderr, never in session_ended
    # itself (docs/QUESTIONS.md Q54's sibling live finding) -- classify_stream just
    # threads an already-redacted string through; classify_kopicode_output owns the
    # redaction itself (test_kopicode_stderr_redaction.py).
    outcome = classify_stream(
        [{"kind": "session_ended", "reason": "error", "exit_code": 3}],
        stderr_tail="401 Unauthorized: API key expired",
    )
    assert outcome.kind == "failed"
    assert outcome.reason == ("exit_code=3 reason=error; stderr: 401 Unauthorized: API key expired")


def test_a_failed_session_with_no_stderr_omits_the_stderr_clause() -> None:
    outcome = classify_stream(
        [{"kind": "session_ended", "reason": "error", "exit_code": 3}], stderr_tail=""
    )
    assert outcome.reason == "exit_code=3 reason=error"


def test_a_stream_with_no_session_ended_raises() -> None:
    with pytest.raises(DelegationError):
        classify_stream([{"kind": "user_message", "text": "hi"}])


def test_a_successful_write_file_call_is_a_completed_edit() -> None:
    # write_file never emits edit_applied (kopicode's dispatch table only calls
    # journalEdit for edit_file/edit_file_fuzzy) -- only a tool_call_parsed /
    # tool_result pair for it.
    outcome = classify_stream(
        [
            {
                "kind": "tool_call_parsed",
                "tool": "write_file",
                "detail": '{"path":"NOTES.md","content":"hello"}',
            },
            {"kind": "tool_result", "tool": "write_file"},
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert outcome.kind == "completed"
    assert outcome.edited_paths == ["NOTES.md"]


def test_a_successful_delete_file_call_is_a_completed_edit() -> None:
    outcome = classify_stream(
        [
            {"kind": "tool_call_parsed", "tool": "delete_file", "detail": '{"path":"old.txt"}'},
            {"kind": "tool_result", "tool": "delete_file"},
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert outcome.kind == "completed"
    assert outcome.edited_paths == ["old.txt"]


def test_a_failed_write_file_call_is_not_credited_as_an_edit() -> None:
    # A tool_result's `reason` field is only present when journal.ToolResult's
    # ErrorKind is non-empty (print.go omits zero fields), so its presence marks
    # this call as having failed rather than landed.
    outcome = classify_stream(
        [
            {
                "kind": "tool_call_parsed",
                "tool": "write_file",
                "detail": '{"path":"NOTES.md","content":"hello"}',
            },
            {"kind": "tool_result", "tool": "write_file", "reason": "task"},
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert outcome.kind == "completed"
    assert outcome.edited_paths == []


def test_an_allow_decision_is_not_treated_as_a_denial() -> None:
    outcome = classify_stream(
        [
            {"kind": "permission_decided", "decision": "allow", "reason": "on the allowlist"},
            {"kind": "edit_applied", "path": "a.py"},
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert outcome.kind == "completed"


def test_provider_response_sizes_are_summed_into_tokens() -> None:
    # KAN-1712/ADR-0017: kopicode's own `internal/engine/event.go` copies only
    # `Tokens.Total` into a `provider_response` line's `size` field (verified
    # directly against kopicode's source) -- summed across every such line in
    # the session, since a multi-turn session makes more than one call.
    outcome = classify_stream(
        [
            {"kind": "provider_response", "reason": "stop", "source": "z-ai", "size": 120},
            {"kind": "edit_applied", "path": "a.py"},
            {"kind": "provider_response", "reason": "stop", "source": "z-ai", "size": 45},
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert outcome.kind == "completed"
    assert outcome.tokens == 165
    assert outcome.cost_usd is None  # kopicode reports no dollar figure at all


def test_zero_provider_response_lines_is_zero_tokens_not_none() -> None:
    # An immediate denial before any provider call still burned zero tokens --
    # a real, known answer, not "we don't know."
    outcome = classify_stream(
        [
            {"kind": "permission_decided", "decision": "deny", "reason": "no shell"},
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert outcome.tokens == 0


def test_a_successful_tool_call_is_recorded_as_ok() -> None:
    # KAN-1714/ADR-0019: verified live -- tool_call_parsed/tool_result pairing
    # generalises past the existing edit-detection-only tracking.
    outcome = classify_stream(
        [
            {"kind": "tool_call_parsed", "tool": "write_file", "detail": '{"path":"a.txt"}'},
            {"kind": "tool_result", "tool": "write_file"},
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert outcome.tool_calls == [
        ToolCallRecord(tool="write_file", detail='{"path":"a.txt"}', status="ok")
    ]


def test_a_denied_tool_call_is_recorded_as_denied_not_error() -> None:
    # Verified live: a denial's own tool_result still carries a non-empty
    # `reason` (ErrorKind) -- the preceding permission_decided/deny is what
    # distinguishes "denied" from a genuine execution error.
    outcome = classify_stream(
        [
            {"kind": "tool_call_parsed", "tool": "run_shell", "detail": '{"command":"ls"}'},
            {"kind": "permission_decided", "decision": "deny", "reason": "no shell"},
            {"kind": "tool_result", "tool": "run_shell", "reason": "task"},
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert outcome.tool_calls == [
        ToolCallRecord(tool="run_shell", detail='{"command":"ls"}', status="denied")
    ]


def test_a_failed_tool_call_with_no_denial_is_recorded_as_error() -> None:
    outcome = classify_stream(
        [
            {"kind": "tool_call_parsed", "tool": "edit_file", "detail": '{"path":"a.py"}'},
            {"kind": "tool_result", "tool": "edit_file", "reason": "no_match"},
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert outcome.tool_calls == [
        ToolCallRecord(tool="edit_file", detail='{"path":"a.py"}', status="error")
    ]


def test_multiple_tool_calls_are_recorded_in_order() -> None:
    outcome = classify_stream(
        [
            {"kind": "tool_call_parsed", "tool": "write_file", "detail": '{"path":"a.txt"}'},
            {"kind": "tool_result", "tool": "write_file"},
            {"kind": "tool_call_parsed", "tool": "run_shell", "detail": '{"command":"ls"}'},
            {"kind": "permission_decided", "decision": "deny", "reason": "no shell"},
            {"kind": "tool_result", "tool": "run_shell", "reason": "task"},
            {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        ]
    )
    assert [tc.tool for tc in outcome.tool_calls] == ["write_file", "run_shell"]
    assert [tc.status for tc in outcome.tool_calls] == ["ok", "denied"]
