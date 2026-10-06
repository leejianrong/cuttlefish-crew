from __future__ import annotations

import dataclasses

import pytest

from cuttlefish.episodic.events import (
    ConsentDecided,
    DelegationCompleted,
    DelegationFailed,
    DelegationRefused,
    DelegationStarted,
    EventPayload,
    HandoverWritten,
    LlmCallCompleted,
    LlmCallFailed,
    RequestRaised,
    RequestResolved,
    TaskCompleted,
    TaskFailed,
    TaskSubmitted,
    TeamResumed,
    ToolCallRecorded,
    UnknownPayload,
    decode_payload,
    encode_payload,
)

KNOWN_PAYLOADS: list[EventPayload] = [
    TaskSubmitted(text="add a .gitignore entry for build artifacts"),
    LlmCallCompleted(
        model="claude", prompt="hi", response="hello", input_tokens=3, output_tokens=1
    ),
    LlmCallCompleted(model="claude", prompt="hi", response="hello"),
    LlmCallFailed(model="claude", prompt="hi", error="rate limited"),
    DelegationStarted(task_text="fix the bug", root="/tmp/scratch"),
    DelegationStarted(task_text="fix the bug", root="/tmp/scratch", policy_allow=[["go", "test"]]),
    DelegationStarted(
        task_text="fix the bug",
        root="/tmp/scratch",
        policy_allow=[["go", "test"]],
        sandbox="container",
    ),
    DelegationStarted(
        task_text="fix the bug",
        root="/tmp/scratch",
        project="demo",
        secret_names=["HUGGINGFACE_TOKEN"],
    ),
    DelegationCompleted(summary="added the entry", edited_paths=[".gitignore"]),
    DelegationRefused(reason="run_shell is not on the declared allowlist"),
    DelegationFailed(reason="kopicode binary exited 1"),
    HandoverWritten(summary="working on the gitignore task", covers_seq_from=1, covers_seq_to=8),
    TaskCompleted(result="done"),
    TaskFailed(error="delegation refused"),
    TeamResumed(resumed_from_seq=3),
    ToolCallRecorded(tool="write_file", detail='{"path":"a.txt"}', status="ok"),
    ToolCallRecorded(tool="run_shell", detail='{"command":"ls"}', status="denied", role="builder"),
    ConsentDecided(kind="shell", detail="ls -la", answer="allow", rule="allow[0]"),
    ConsentDecided(kind="shell", detail="rm -rf /", answer="deny", rule="no_match", role="builder"),
    RequestRaised(
        request_id="r1",
        kind="permission",
        title="Builder wants to run a command",
        detail="docker compose up -d postgres",
        why="not on the command list",
        answers=["allow_once", "allow_always", "deny"],
        expires_at="2026-10-06T10:00:00+00:00",
        suggested_rule=["docker", "compose", "up"],
        role="builder",
        backend="kopicode",
    ),
    RequestRaised(
        request_id="r2",
        kind="question",
        title="Which limit?",
        detail="Per request or per session?",
        why="the agent asked",
        answers=["reply"],
        expires_at="2026-10-06T10:00:00+00:00",
        lands="end_of_turn",
    ),
    RequestResolved(request_id="r1", resolution="allowed_always", by="person", rule=["docker"]),
    RequestResolved(request_id="r2", resolution="expired", by="timeout"),
]


@pytest.mark.parametrize("payload", KNOWN_PAYLOADS)
def test_known_payload_round_trips(payload: EventPayload) -> None:
    event_type, data = encode_payload(payload)
    assert event_type == payload.EVENT_TYPE  # type: ignore[union-attr]
    decoded = decode_payload(event_type, data)
    assert decoded == payload


def test_unrecognised_event_type_becomes_unknown_payload() -> None:
    decoded = decode_payload("SomeFutureEvent", {"whatever": "shape", "n": 3})
    assert isinstance(decoded, UnknownPayload)
    assert decoded.event_type == "SomeFutureEvent"
    assert decoded.data == {"whatever": "shape", "n": 3}


def test_unknown_payload_round_trips_verbatim() -> None:
    original = UnknownPayload(event_type="SomeFutureEvent", data={"whatever": "shape"})
    event_type, data = encode_payload(original)
    assert event_type == "SomeFutureEvent"
    assert data == {"whatever": "shape"}
    decoded = decode_payload(event_type, data)
    assert decoded == original


def test_a_pre_slice_b_delegation_started_event_still_decodes() -> None:
    """A `DelegationStarted` written before ADR-0006 added `project`/
    `secret_names` has neither key in its `data` dict at all -- must decode to
    the one scope every task implicitly ran under then, not raise."""
    pre_slice_b_data = {
        "task_text": "fix the bug",
        "root": "/tmp/scratch",
        "policy_allow": None,
        "sandbox": None,
        "backend": "kopicode",
    }
    decoded = decode_payload("DelegationStarted", pre_slice_b_data)
    assert isinstance(decoded, DelegationStarted)
    assert decoded.project == "default"
    assert decoded.secret_names == []


def test_decode_ignores_a_field_a_known_type_does_not_declare() -> None:
    # A newer build added a field this one has never heard of. Decoding must not
    # raise -- "compatible for readers, not for rewriters" (events.py docstring).
    data = dataclasses.asdict(TaskSubmitted(text="hello")) | {"future_field": "value"}
    decoded = decode_payload("TaskSubmitted", data)
    assert decoded == TaskSubmitted(text="hello")
