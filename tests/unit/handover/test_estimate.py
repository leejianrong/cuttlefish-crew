from __future__ import annotations

from cuttlefish.episodic.events import (
    ConsentDecided,
    DelegationCompleted,
    DelegationFailed,
    TaskCompleted,
    TaskSubmitted,
    ToolCallRecorded,
)
from cuttlefish.handover import _build_summary_prompt, estimate_event_tokens, estimate_tokens


def test_estimate_tokens_is_roughly_length_over_four() -> None:
    assert estimate_tokens("a" * 40) == 10


def test_estimate_tokens_never_returns_zero_for_nonempty_text() -> None:
    assert estimate_tokens("hi") == 1


def test_estimate_event_tokens_sums_every_text_field() -> None:
    payload = TaskCompleted(result="a" * 40)
    assert estimate_event_tokens(payload) == 10


def test_estimate_event_tokens_zero_for_a_payload_with_no_text_fields() -> None:
    # No such payload exists among the known types today, but decode_payload's
    # UnknownPayload does carry a `data` dict rather than a text field, and
    # estimate_event_tokens must not raise on it.
    from cuttlefish.episodic.events import UnknownPayload

    assert estimate_event_tokens(UnknownPayload(event_type="Future", data={"a": 1})) == 0


def test_delegation_failed_reason_counts() -> None:
    payload = DelegationFailed(reason="b" * 20)
    assert estimate_event_tokens(payload) == 5


def test_task_submitted_text_counts() -> None:
    payload = TaskSubmitted(text="c" * 12)
    assert estimate_event_tokens(payload) == 3


def test_a_rounds_tool_calls_and_edits_count_and_reach_the_summary_prompt() -> None:
    window = [
        (1, DelegationFailed(reason="stop=max_turns exit_code=4", role="b", detail="boom")),
        (
            2,
            ToolCallRecorded(tool="write_file", detail='{"path":"one.txt"}', status="ok", role="b"),
        ),
        (3, ConsentDecided(kind="run_shell", detail="rm x", answer="deny", rule="no", role="b")),
        (4, ConsentDecided(kind="run_shell", detail="ls", answer="allow", rule="ok", role="b")),
        (5, DelegationCompleted(summary="done", edited_paths=["a.py", "b.py"], role="b")),
    ]
    prompt = _build_summary_prompt(window)
    assert "boom" in prompt
    assert 'write_file ok: {"path":"one.txt"}' in prompt
    assert "refused: rm x" in prompt
    assert "4. ConsentDecided" not in prompt  # an allow says nothing, so it is left out
    assert "edited: a.py, b.py" in prompt
    assert "Done (name the files and modules)" in prompt
    assert all(estimate_event_tokens(payload) > 0 for _, payload in window[:3] + window[4:])
    assert estimate_event_tokens(window[3][1]) == 0  # an allowed command is not progress


def test_a_round_that_ran_out_of_room_is_not_summarised_as_a_failure() -> None:
    from cuttlefish.episodic.events import DelegationFailed

    prompt = _build_summary_prompt(
        [
            (1, DelegationFailed(reason="stop=max_turns exit_code=4", failure_kind="max_turns")),
            (2, DelegationFailed(reason="boom", failure_kind="provider_auth")),
        ]
    )
    assert "ran out of room (max_turns); the work continues" in prompt
    assert "stop=max_turns" not in prompt and "boom" in prompt
