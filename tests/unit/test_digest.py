"""Unit: the manager's digest carries no code, output or secrets, and stays small (CUT-150)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from cuttlefish.episodic.events import (
    AutopilotUpdated,
    ConsentDecided,
    DelegationCompleted,
    DelegationFailed,
    DelegationStarted,
    EventPayload,
    RequestRaised,
    TaskFailed,
    ToolCallRecorded,
)
from cuttlefish.episodic.store import EpisodicEvent
from cuttlefish.fleet.digest import MAX_BYTES, TEXT_CHARS, build_digest

SECRET = "planted-secret-value-0123456789"
SOURCE = "def planted_function_body(): return 'proprietary_source_marker'"


def journal(*payloads: EventPayload) -> list[EpisodicEvent]:
    start = datetime(2026, 10, 31, 9, tzinfo=UTC)
    return [
        EpisodicEvent("t", i, 1, start + timedelta(seconds=i), p) for i, p in enumerate(payloads)
    ]


def request(kind: str = "permission", title: str = "Run a command") -> RequestRaised:
    return RequestRaised(
        request_id="r1",
        kind=kind,  # type: ignore[arg-type]
        title=title,
        detail=f"curl -H 'X: {SECRET}' http://x",
        why=SOURCE,
        answers=["allow"],
        expires_at="2026-10-31T10:00:00+00:00",
        role="builder",
    )


def test_status_events_pending_and_spend() -> None:
    d = build_digest(
        project_id="p",
        name="Shop",
        events=journal(
            DelegationStarted(task_text="t", root="/r", role="builder"),
            DelegationCompleted(
                summary="s", edited_paths=["a.py", "b.py"], role="builder", tokens=900
            ),
            DelegationStarted(task_text="t", root="/r", role="builder"),
        ),
        pending=[request()],
    )
    assert d["status"] == "waiting"
    assert d["roles"] == {"builder": "working"}
    assert d["spend"] == {"tokens": 900, "cost_usd": None}
    assert d["pending_count"] == 1
    assert d["pending"][0]["request_id"] == "r1"
    assert [e["text"] for e in d["last_events"]] == [
        "Round started on kopicode",
        "Round finished, 2 files edited",
        "Round started on kopicode",
    ]


def test_status_stuck_and_blockers() -> None:
    d = build_digest(
        project_id="p",
        name="Shop",
        events=journal(
            AutopilotUpdated(
                phase="stuck", goal="ship", backend="claude-code", reason="no progress"
            )
        ),
        pending=[request("blocked", "Needs an API key")],
    )
    assert d["status"] == "stuck"
    assert d["blockers"] == ["Needs an API key", "Autopilot stuck: no progress"]
    assert d["pending"] == []  # a blocker is listed once, as a blocker
    assert d["pending_count"] == 1


def test_done_and_idle() -> None:
    assert build_digest(project_id="p", name="n", events=[], pending=[])["status"] == "idle"
    done = build_digest(
        project_id="p",
        name="n",
        events=journal(AutopilotUpdated(phase="done", goal="g", backend="b")),
        pending=[],
    )
    assert done["status"] == "done"


def test_no_code_output_or_secret_gets_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    events = journal(
        DelegationStarted(task_text=SOURCE, root="/r", role="builder"),
        ToolCallRecorded(tool="run_shell", detail=f"cat secrets.py {SOURCE}", status="ok"),
        ConsentDecided(kind="shell", detail=SOURCE, answer="allow", rule=SOURCE),
        DelegationCompleted(
            summary=SOURCE, edited_paths=[SOURCE, "src/planted.py"], role="builder"
        ),
        DelegationFailed(
            reason=SOURCE, detail=f"{SOURCE} {SECRET}", record="/home/x/rec", role="builder"
        ),
        TaskFailed(error=f"{SOURCE} {SECRET}", role="builder"),
    )
    pending = [request(title=f"Run: curl {SECRET}")]
    text = json.dumps(build_digest(project_id="p", name="n", events=events, pending=pending))
    assert SECRET not in text
    assert "proprietary_source_marker" not in text
    assert "planted_function_body" not in text
    assert "src/planted.py" not in text
    assert "/home/x/rec" not in text
    assert "[redacted:OPENROUTER_API_KEY]" in text  # a title is kept, its secret scrubbed


def test_free_text_is_truncated_and_the_size_is_bounded() -> None:
    huge = "x" * 100_000
    many = [
        RequestRaised(
            request_id=str(i),
            kind="question",
            title=huge,
            detail=huge,
            why=huge,
            answers=[],
            expires_at="e",
            role="r",
        )
        for i in range(500)
    ]
    d = build_digest(project_id="p", name=huge, events=journal(*many), pending=many, last_n=10_000)
    assert len(d["last_events"]) == 25
    assert len(d["pending"]) == 25 and d["pending_count"] == 500
    assert len(d["pending"][0]["title"]) <= TEXT_CHARS
    assert len(json.dumps(d).encode()) < MAX_BYTES
