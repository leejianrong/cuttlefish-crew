"""Unit: the chat thread read from kopicode's events.jsonl (CUT-178), in the shape of v0.5."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cuttlefish.sessions.kopicode_record import find_events, items_for, read_events


def event(kind: str, seq: int, **payload: Any) -> dict[str, Any]:
    return {
        "session_id": "s1",
        "seq": seq,
        "type": kind,
        "ts": "2026-10-22T10:00:00.000Z",
        "payload": payload,
    }


def inline(text: str) -> dict[str, Any]:
    return {"inline": text, "size": len(text)}


def test_messages_become_user_and_assistant_items() -> None:
    said = items_for(event("UserMessage", 2, text=inline("Fix it")))
    replied = items_for(event("AssistantMessage", 5, text=inline("On it.")))
    assert [(i["kind"], i["text"], i["id"]) for i in said + replied] == [
        ("user", "Fix it", "s1:2"),
        ("assistant", "On it.", "s1:5"),
    ]
    assert items_for(event("AssistantMessage", 6, text=inline("  "))) == []


def test_a_call_and_its_result_share_the_call_id_and_names_are_the_pages() -> None:
    call = items_for(
        event("ToolCallParsed", 7, call_id="c1", tool="run_shell", args={"command": "ls -la"})
    )[0]
    ok = items_for(event("ToolResult", 8, call_id="c1", tool="run_shell", output=inline("a.py")))[0]
    bad = items_for(
        event(
            "ToolResult", 9, call_id="c2", tool="run_shell", output=inline("no"), error_kind="exit"
        )
    )[0]
    assert (call["name"], call["summary"], call["tool_id"]) == ("Bash", "ls -la", "c1")
    assert (ok["tool_id"], ok["text"], ok["is_error"]) == ("c1", "a.py", False)
    assert bad["is_error"] is True
    read = items_for(
        event("ToolCallParsed", 10, call_id="c3", tool="read_file", args={"path": "README.md"})
    )[0]
    assert (read["name"], read["summary"]) == ("Read", "README.md")


def test_a_spilled_output_is_a_note_of_its_size_not_a_crash() -> None:
    result = items_for(
        event("ToolResult", 11, call_id="c1", tool="run_shell", output={"size": 90000})
    )[0]
    assert "90000 bytes" in result["text"]


def test_a_question_to_the_person_is_shown_and_provider_traffic_is_not() -> None:
    asked = items_for(event("AskRequested", 12, call_id="a1", question=inline("Which branch?")))
    assert [(i["kind"], i["text"]) for i in asked] == [("assistant", "Which branch?")]
    for kind in (
        "ProviderRequest",
        "ProviderResponse",
        "PermissionRequested",
        "SessionStarted",
        "Nope",
    ):
        assert items_for(event(kind, 1, body=inline("x"))) == []


def test_the_newest_session_written_since_the_start_is_the_one(tmp_path: Path) -> None:
    sessions = tmp_path / ".kopicode" / "sessions"
    for name, mtime in (("old", 1000.0), ("new", 2000.0)):
        (sessions / name).mkdir(parents=True)
        path = sessions / name / "events.jsonl"
        path.write_text("{}\n")
        os.utime(path, (mtime, mtime))
    assert find_events(str(tmp_path)) == sessions / "new" / "events.jsonl"
    assert find_events(str(tmp_path), not_before=3000.0) is None
    assert find_events(str(tmp_path / "missing")) is None


def test_events_are_read_from_the_cursor(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    lines = [event("SessionStarted", 1), event("UserMessage", 2, text=inline("one"))]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    first = read_events(path, None)
    assert [i["text"] for i in first.items] == ["one"]
    with path.open("a") as handle:
        handle.write(json.dumps(event("AssistantMessage", 3, text=inline("two"))) + "\n")
    assert [i["text"] for i in read_events(path, first.cursor).items] == ["two"]
