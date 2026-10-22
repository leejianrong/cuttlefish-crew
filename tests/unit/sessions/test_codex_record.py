"""Unit: the chat thread read from Codex's rollout (CUT-177), in the shape of codex-cli 0.155."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cuttlefish.sessions.codex_record import find_rollout, items_for, read_rollout


def done(item: dict[str, Any], at: str = "2026-10-22T10:00:00.000Z") -> dict[str, Any]:
    return {
        "timestamp": at,
        "type": "event_msg",
        "payload": {"type": "item_completed", "item": item},
    }


def say(kind: str, ident: str, text: str) -> dict[str, Any]:
    part = {"type": "text" if kind == "UserMessage" else "Text", "text": text}
    return done({"type": kind, "id": ident, "content": [part]})


def command(ident: str, line: str, output: str, code: object, status: str) -> dict[str, Any]:
    return done(
        {
            "type": "CommandExecution",
            "id": ident,
            "command": ["/bin/bash", "-lc", line],
            "aggregated_output": output,
            "exit_code": code,
            "status": status,
        }
    )


def test_what_was_said_becomes_messages_and_reasoning_is_left_out() -> None:
    items = items_for(say("UserMessage", "u1", "Fix it")) + items_for(
        say("AgentMessage", "a1", "On it.")
    )
    assert [(i["kind"], i["text"]) for i in items] == [("user", "Fix it"), ("assistant", "On it.")]
    assert items_for(done({"type": "Reasoning", "id": "r", "summary_text": []})) == []
    assert items_for(done({"type": "AgentMessage", "id": "a2", "content": []})) == []


def test_a_command_is_a_call_with_its_result_and_a_failure_is_marked() -> None:
    ok = items_for(command("e1", "uv run pytest -q", "41 passed", 0, "completed"))
    assert [(i["kind"], i.get("summary") or i.get("text")) for i in ok] == [
        ("tool", "uv run pytest -q"),
        ("tool_result", "41 passed"),
    ]
    assert ok[0]["name"] == "Bash" and ok[0]["tool_id"] == ok[1]["tool_id"]
    assert not ok[1]["is_error"]
    bad = items_for(command("e2", "git status", "fatal", "128", "failed"))
    assert bad[1]["is_error"] is True


def test_file_changes_are_writes_or_edits_one_call_per_file() -> None:
    changes = {"/w/.cuttlefish/artifacts/a.png": {"type": "add"}, "/w/x.py": {"type": "update"}}
    items = items_for(
        done(
            {
                "type": "FileChange",
                "id": "f1",
                "status": "completed",
                "stdout": "ok",
                "changes": changes,
            }
        )
    )
    calls = [i for i in items if i["kind"] == "tool"]
    assert [(c["name"], c["summary"]) for c in calls] == [
        ("Write", "/w/.cuttlefish/artifacts/a.png"),
        ("Edit", "/w/x.py"),
    ]


def test_a_compaction_is_a_divider_and_bookkeeping_is_nothing() -> None:
    assert items_for(done({"type": "ContextCompaction", "id": "c"}))[0]["kind"] == "divider"
    assert items_for({"type": "token_usage_record", "payload": {}}) == []
    assert items_for({"type": "event_msg", "payload": {"type": "task_started"}}) == []
    assert items_for(done({"type": "Something new", "id": "z"})) == []


def _rollout(home: Path, cwd: str, name: str, mtime: float, day: str = "") -> Path:
    stamp = day or datetime.now(UTC).strftime("%Y/%m/%d")
    folder = home / ".codex" / "sessions" / stamp
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-{name}.jsonl"
    path.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": cwd}}) + "\n")
    os.utime(path, (mtime, mtime))
    return path


def test_the_rollout_is_found_by_the_folder_and_the_newest_wins(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    _rollout(tmp_path, str(root), "old", 1000.0)
    newer = _rollout(tmp_path, str(root), "new", 2000.0)
    _rollout(tmp_path, "/somewhere/else", "other", 3000.0)
    assert find_rollout(str(root), home=tmp_path) == newer
    assert find_rollout("/nowhere", home=tmp_path) is None


def test_a_record_older_than_the_session_is_not_this_sessions(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    _rollout(tmp_path, str(root), "before", 1000.0)
    assert find_rollout(str(root), home=tmp_path, not_before=1500.0) is None


def test_a_rollout_is_read_from_the_cursor(tmp_path: Path) -> None:
    path = tmp_path / "r.jsonl"
    lines = [{"type": "session_meta", "payload": {"cwd": "/x"}}, say("UserMessage", "u1", "one")]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    first = read_rollout(path, None)
    assert [i["text"] for i in first.items] == ["one"]
    with path.open("a") as handle:
        handle.write(
            json.dumps(
                done(
                    {
                        "type": "AgentMessage",
                        "id": "a",
                        "content": [{"type": "Text", "text": "two"}],
                    }
                )
            )
            + "\n"
        )
    assert [i["text"] for i in read_rollout(path, first.cursor).items] == ["two"]
