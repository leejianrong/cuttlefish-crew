"""Unit: the chat thread read from Claude Code's transcript (CUT-172). The lines are made up in the
shape observed on Claude Code 2.1.300 to 2.1.308."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cuttlefish.sessions.transcript import (
    WANTED_ITEMS,
    WINDOW_BYTES,
    candidate_paths,
    items_for,
    project_dir_name,
    read_thread,
    summarise_call,
)


def _user(text: str, uuid: str = "u1", **extra: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": "2026-10-22T10:00:00.000Z",
        "message": {"role": "user", "content": text},
        **extra,
    }


def _assistant(blocks: list[dict[str, Any]], uuid: str = "a1") -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": "2026-10-22T10:00:01.000Z",
        "message": {"role": "assistant", "id": "msg_1", "content": blocks},
    }


def _result(tool_id: str, content: Any, uuid: str = "r1", error: bool = False) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": "2026-10-22T10:00:02.000Z",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": content,
                    "is_error": error,
                }
            ],
        },
    }


def _write(path: Path, entries: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


def test_a_folder_is_named_from_its_path_the_way_claude_code_names_it() -> None:
    assert project_dir_name("/home/jo/projects/abang-ai/x") == "-home-jo-projects-abang-ai-x"
    assert project_dir_name("/home/jo/.claude/skills") == "-home-jo--claude-skills"
    assert project_dir_name("/tmp/a_b.c") == "-tmp-a-b-c"


def test_candidate_paths_cover_the_resolved_folder_and_the_one_given(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    paths = candidate_paths(str(link), "sid", home=Path("/h"))
    assert [p.parent.name for p in paths] == [
        project_dir_name(str(real)),
        project_dir_name(str(link)),
    ]
    assert paths[0].name == "sid.jsonl"


def test_what_the_person_and_the_agent_said_become_items() -> None:
    assert items_for(_user("Fix the bug"))[0] | {"at": None} == {
        "id": "u1",
        "kind": "user",
        "at": None,
        "text": "Fix the bug",
    }
    said = items_for(_assistant([{"type": "text", "text": "On it."}]))
    assert [(i["kind"], i["text"]) for i in said] == [("assistant", "On it.")]


def test_a_tool_call_is_one_line_and_its_result_is_keyed_to_it() -> None:
    call = items_for(
        _assistant(
            [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls  -la\n"}}]
        )
    )[0]
    assert (call["kind"], call["tool_id"], call["name"], call["summary"]) == (
        "tool",
        "t1",
        "Bash",
        "ls -la",
    )
    result = items_for(_result("t1", [{"type": "text", "text": "a.py"}], error=True))[0]
    assert (result["kind"], result["tool_id"], result["text"], result["is_error"]) == (
        "tool_result",
        "t1",
        "a.py",
        True,
    )


def test_thinking_meta_sidechains_and_bookkeeping_are_left_out() -> None:
    assert items_for(_assistant([{"type": "thinking", "thinking": "", "signature": "x"}])) == []
    assert items_for(_user("<local-command-caveat>…", isMeta=True)) == []
    assert items_for(_user("<system-reminder>x</system-reminder>")) == []
    assert items_for(_user("sub-agent chatter", isSidechain=True)) == []
    assert items_for({"type": "api-request", "id": "x"}) == []
    assert items_for({"type": "system", "subtype": "turn_duration"}) == []


def test_a_slash_command_is_shown_as_a_command() -> None:
    entry = _user("<command-name>/compact</command-name><command-args>keep tests</command-args>")
    item = items_for(entry)[0]
    assert (item["kind"], item["text"]) == ("command", "/compact keep tests")


def test_a_context_refresh_is_a_divider() -> None:
    assert (
        items_for({"type": "system", "subtype": "compact_boundary", "uuid": "c"})[0]["kind"]
        == "divider"
    )
    assert items_for(_user("summary…", isCompactSummary=True))[0]["kind"] == "divider"
    assert (
        items_for({"type": "continued-in", "uuid": "c"})[0]["text"] == "Continued in a new session"
    )


def test_the_agent_own_recap_is_a_note() -> None:
    note = items_for(
        {"type": "system", "subtype": "away_summary", "uuid": "n", "content": "Goal: x"}
    )
    assert (note[0]["kind"], note[0]["text"]) == ("note", "Goal: x")


def test_summaries_pick_the_useful_input() -> None:
    assert summarise_call("Read", {"file_path": "/a/b.py", "limit": 5}) == "/a/b.py"
    assert summarise_call("Grep", {"pattern": "foo", "path": "/a"}) == "foo"
    assert summarise_call("Odd", {"n": 1, "label": "hello"}) == "hello"
    assert summarise_call("None", {}) == ""
    assert len(summarise_call("Bash", {"command": "x" * 1000})) == 240


def test_reading_again_from_the_cursor_returns_only_what_was_appended(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    _write(path, [_user("one"), _assistant([{"type": "text", "text": "two"}], uuid="a2")])
    first = read_thread(path)
    assert [i["text"] for i in first.items] == ["one", "two"]
    assert read_thread(path, first.cursor).items == []
    with path.open("a") as handle:
        handle.write(json.dumps(_user("three", uuid="u3")) + "\n")
    more = read_thread(path, first.cursor)
    assert [i["text"] for i in more.items] == ["three"]


def test_a_line_still_being_written_is_left_for_next_time(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    whole = json.dumps(_user("done", uuid="u1")) + "\n"
    half = json.dumps(_user("later", uuid="u2"))
    path.write_text(whole + half[:20])
    first = read_thread(path)
    assert [i["text"] for i in first.items] == ["done"]
    path.write_text(whole + half + "\n")
    assert [i["text"] for i in read_thread(path, first.cursor).items] == ["later"]


def test_a_line_that_is_not_json_is_counted_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text("not json\n" + json.dumps(_user("ok")) + "\n[1,2]\n")
    thread = read_thread(path)
    assert [i["text"] for i in thread.items] == ["ok"] and thread.skipped == 2


def test_a_first_read_of_a_big_file_starts_near_the_end_on_a_whole_line(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    filler = {"type": "api-request-blob", "message": "x" * 900}
    count = WINDOW_BYTES // 900 + 200
    talk = [_user(f"message {n}", uuid=f"m{n}") for n in range(WANTED_ITEMS + 5)]
    _write(path, [filler] * count + talk)
    thread = read_thread(path)
    assert thread.earlier is True
    assert [i["text"] for i in thread.items] == [f"message {n}" for n in range(WANTED_ITEMS + 5)]
    assert thread.cursor == path.stat().st_size


def test_a_first_read_widens_its_window_when_the_end_is_mostly_bookkeeping(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    filler = {"type": "api-request-blob", "message": "x" * 900}
    spread: list[dict[str, Any]] = []
    for n in range(10):  # a message, then more than a window of bookkeeping, ten times over
        spread.append(_user(f"early {n}", uuid=f"e{n}"))
        spread.extend([filler] * (WINDOW_BYTES // 900 + 50))
    _write(path, spread)
    thread = read_thread(path)
    assert len(thread.items) > 1 and thread.items[-1]["text"] == "early 9"
    assert thread.cursor == path.stat().st_size


def test_a_file_that_got_shorter_asks_the_client_to_start_again(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    _write(path, [_user("fresh")])
    thread = read_thread(path, after=10_000)
    assert thread.reset is True and [i["text"] for i in thread.items] == ["fresh"]


def test_a_known_secret_is_scrubbed_from_every_item(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    _write(path, [_assistant([{"type": "text", "text": "the value is SECRETVALUE1234"}])])
    thread = read_thread(path, scrub=lambda t: t.replace("SECRETVALUE1234", "[redacted:X]"))
    assert thread.items[0]["text"] == "the value is [redacted:X]"
