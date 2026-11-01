"""Tokens per prompt read from a Claude Code transcript (CUT-273)."""

import json
from pathlib import Path

from cuttlefish.sessions.usage import read_file, read_turns


def _user(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}})


def _assistant(mid: str, cache_read: int, out: int = 10) -> str:
    usage = {
        "input_tokens": 3,
        "output_tokens": out,
        "cache_creation_input_tokens": 100,
        "cache_read_input_tokens": cache_read,
    }
    return json.dumps({"type": "assistant", "message": {"id": mid, "usage": usage}})


def test_a_message_split_over_lines_is_counted_once_and_tool_results_are_not_prompts() -> None:
    tool_result = json.dumps(
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}}
    )
    lines = [
        _user("Approvals are waiting"),
        _assistant("m1", 1000),
        _assistant("m1", 1000),  # a second block of the same reply
        tool_result,
        _assistant("m2", 2000),
        "not json",
        _user("Check in"),
        _assistant("m3", 500),
    ]
    first, second = read_turns(lines)
    assert (first.requests, first.cache_read, first.cache_create) == (2, 3000, 200)
    assert first.output == 20 and first.total == 3000 + 200 + 20 + 6
    assert (second.requests, second.cache_read, second.prompt) == (1, 500, "Check in")


def test_read_file_reads_a_transcript(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join([_user("hi"), _assistant("m", 7)]) + "\n")
    assert [t.cache_read for t in read_file(path)] == [7]
