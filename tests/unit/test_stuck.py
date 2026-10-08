"""Unit: the stuck-agent detector and the session-record reader (ADR-0029, V5-E5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cuttlefish.stuck import (
    DEFAULT_THRESHOLD,
    SessionRecord,
    ShellResult,
    StuckDetector,
    threshold_from_env,
)

MISSING = ShellResult(
    1, "run_shell `pytest`: exited 1\nModuleNotFoundError: No module named 'numpy'"
)
OTHER = ShellResult(1, "FAILED tests/test_x.py::test_y - assert 1 == 2")
OK = ShellResult(0, "3 passed")


def test_n_consecutive_environment_failures_give_a_verdict_with_the_evidence() -> None:
    detector = StuckDetector(3)
    assert detector.feed(MISSING) is None
    assert detector.feed(MISSING) is None
    verdict = detector.feed(MISSING)
    assert verdict is not None
    assert verdict.count == 3
    assert "No module named 'numpy'" in verdict.evidence


def test_a_success_or_an_unrelated_failure_resets_the_count() -> None:
    detector = StuckDetector(3)
    for result in (MISSING, MISSING, OTHER, MISSING, MISSING, OK, MISSING, MISSING):
        assert detector.feed(result) is None
    assert detector.feed(MISSING) is not None


def test_a_zero_exit_that_mentions_a_signature_is_not_a_failure() -> None:
    detector = StuckDetector(1)
    assert detector.feed(ShellResult(0, "grep: No module named x (in a log line)")) is None


def test_zero_turns_the_detector_off() -> None:
    detector = StuckDetector(0)
    assert not detector.enabled
    assert all(detector.feed(MISSING) is None for _ in range(20))


def test_signatures_are_data() -> None:
    detector = StuckDetector(1, signatures=[r"licence server unreachable"])
    assert detector.feed(MISSING) is None
    assert detector.feed(ShellResult(2, "Licence Server Unreachable")) is not None


def test_each_toolchain_signature_matches() -> None:
    for text in (
        "bash: pnpm: command not found",
        "Error: Cannot find module 'left-pad'",
        "Error: spawn tsc ENOENT",
        'exec: "go": executable file not found in $PATH',
        "ERROR: No matching distribution found for foo",
    ):
        assert StuckDetector(1).feed(ShellResult(127, text)) is not None, text


def test_the_threshold_comes_from_the_environment() -> None:
    assert threshold_from_env({}) == DEFAULT_THRESHOLD
    assert threshold_from_env({"CUTTLEFISH_STUCK_THRESHOLD": "2"}) == 2
    assert threshold_from_env({"CUTTLEFISH_STUCK_THRESHOLD": "0"}) == 0
    assert threshold_from_env({"CUTTLEFISH_STUCK_THRESHOLD": "-3"}) == 0
    assert threshold_from_env({"CUTTLEFISH_STUCK_THRESHOLD": "lots"}) == DEFAULT_THRESHOLD


def _line(tool: str, code: int | None, output: dict[str, Any]) -> str:
    payload: dict[str, Any] = {"call_id": "c", "tool": tool, "output": output}
    if code is not None:
        payload["exit_code"] = code
    return json.dumps({"type": "ToolResult", "seq": 1, "payload": payload}) + "\n"


def test_the_record_is_read_incrementally_and_a_half_written_line_waits(tmp_path: Path) -> None:
    session = tmp_path / ".kopicode" / "sessions" / "s1"
    session.mkdir(parents=True)
    path = session / "events.jsonl"
    reader = SessionRecord(str(tmp_path), "s1")
    assert reader.new_shell_results() == []  # no file yet: no evidence, no error

    first = _line("run_shell", 1, {"inline": "No module named x", "size": 17})
    other_tool = _line("read_file", 0, {"inline": "x", "size": 1})
    path.write_text(first + other_tool + _line("run_shell", 0, {"inline": "ok"})[:20])
    assert reader.new_shell_results() == [ShellResult(1, "No module named x")]

    path.write_text(first + other_tool + _line("run_shell", 0, {"inline": "ok"}))
    assert reader.new_shell_results() == [ShellResult(0, "ok")]
    assert reader.new_shell_results() == []


def test_a_spilled_output_is_read_from_the_end_of_its_blob(tmp_path: Path) -> None:
    root = tmp_path / ".kopicode"
    (root / "sessions" / "s1").mkdir(parents=True)
    (root / "blobs").mkdir()
    (root / "blobs" / "ab12").write_text("x" * 100_000 + "\nNo module named 'numpy'")
    (root / "sessions" / "s1" / "events.jsonl").write_text(
        _line("run_shell", 1, {"blob": "ab12", "size": 100_000})
        + _line("run_shell", 1, {"blob": "../../etc/passwd", "size": 1})
    )
    first, second = SessionRecord(str(tmp_path), "s1").new_shell_results()
    assert first.output.endswith("No module named 'numpy'")
    assert len(first.output) < 20_000
    assert second.output == ""  # a blob is a name, never a path


def test_written_paths_reads_whole_file_writes_the_tool_did_not_fail_on(tmp_path: Path) -> None:
    import json

    from cuttlefish.stuck import SessionRecord

    def line(kind: str, **payload: object) -> str:
        return json.dumps({"type": kind, "payload": payload})

    folder = tmp_path / ".kopicode" / "sessions" / "s1"
    folder.mkdir(parents=True)
    (folder / "events.jsonl").write_text(
        "\n".join(
            [
                line("ToolCallParsed", call_id="1", tool="write_file", args={"path": "a.py"}),
                line("ToolResult", call_id="1", tool="write_file"),
                line("ToolCallParsed", call_id="2", tool="write_file", args={"path": "bad.py"}),
                line("ToolResult", call_id="2", tool="write_file", error_kind="denied"),
                line("ToolCallParsed", call_id="3", tool="delete_file", args={"path": "old.py"}),
                line("ToolResult", call_id="3", tool="delete_file"),
                line("ToolCallParsed", call_id="4", tool="read_file", args={"path": "r.py"}),
                line("ToolResult", call_id="4", tool="read_file"),
                line("ToolCallParsed", call_id="5", tool="write_file", args={"path": "a.py"}),
                line("ToolResult", call_id="5", tool="write_file"),
                "not json",
            ]
        )
        + "\n"
    )
    assert SessionRecord(str(tmp_path), "s1").written_paths() == ["a.py", "old.py"]
    assert SessionRecord(str(tmp_path), "nope").written_paths() == []
