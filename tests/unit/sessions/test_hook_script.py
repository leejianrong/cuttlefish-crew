"""Unit: the command Claude Code runs for a hook (CUT-127, ADR-0040).

Run as a real subprocess against a stub daemon. The rule under test: it prints a decision only when
the daemon gave one in this response, and anything wrong at all prints nothing and exits 0, so
Claude Code shows its own dialog."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from cuttlefish.sessions.hooks import HOOK_SCRIPT


class Stub:
    def __init__(self) -> None:
        self.reply: bytes = b"{}"
        self.status = 200
        self.delay = 0.0
        self.seen: list[dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                stub.seen.append(
                    {
                        "path": self.path,
                        "token": self.headers.get("x-cuttlefish-hook-token"),
                        "body": json.loads(body),
                    }
                )
                time.sleep(stub.delay)
                self.send_response(stub.status)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(stub.reply)

            def log_message(self, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def says(self, **answer: object) -> None:
        self.reply = json.dumps(answer).encode()


@pytest.fixture
def stub() -> Iterator[Stub]:
    s = Stub()
    yield s
    s.server.shutdown()


@pytest.fixture
def home(tmp_path: Path, stub: Stub) -> Path:
    (tmp_path / ".cuttlefish").mkdir()
    (tmp_path / ".cuttlefish" / "daemon.json").write_text(json.dumps({"url": stub.url, "pid": 1}))
    (tmp_path / ".cuttlefish" / "hook-token").write_text("tok-123\n")
    return tmp_path


def run(home: Path, payload: object, *, timeout: str = "5", raw: str | None = None):  # type: ignore[no-untyped-def]
    return subprocess.run(
        [sys.executable, "-I", str(HOOK_SCRIPT), "--project", "p1", "--timeout", timeout],
        input=raw if raw is not None else json.dumps(payload),
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        timeout=30,
    )


PERMISSION = {
    "hook_event_name": "PermissionRequest",
    "tool_name": "Bash",
    "tool_input": {"command": "ls"},
}


def test_an_allow_from_the_daemon_is_printed_as_claude_codes_decision(
    home: Path, stub: Stub
) -> None:
    stub.says(decision="allow")
    done = run(home, PERMISSION)
    assert done.returncode == 0
    assert json.loads(done.stdout) == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        }
    }
    # It sent the project in the path and its own token, and nothing else of the home.
    assert (
        stub.seen[0]["path"] == "/api/hooks/claude-code/p1" and stub.seen[0]["token"] == "tok-123"
    )


def test_a_deny_carries_its_message_cut_to_a_sentence(home: Path, stub: Stub) -> None:
    stub.says(decision="deny", message="no " * 400)
    decision = json.loads(run(home, PERMISSION).stdout)["hookSpecificOutput"]["decision"]
    assert decision["behavior"] == "deny" and 0 < len(decision["message"]) <= 500


@pytest.mark.parametrize(
    "answer", [{}, {"decision": "yolo"}, {"decision": None}, {"decision": ["allow"]}]
)
def test_no_decision_from_the_daemon_prints_nothing(
    home: Path, stub: Stub, answer: dict[str, Any]
) -> None:
    stub.says(**answer)
    done = run(home, PERMISSION)
    assert done.returncode == 0 and done.stdout == ""


@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop"])
def test_an_allow_for_any_other_event_is_never_printed(home: Path, stub: Stub, event: str) -> None:
    stub.says(decision="allow")
    done = run(home, {**PERMISSION, "hook_event_name": event})
    assert done.returncode == 0 and done.stdout == ""


def test_a_daemon_that_is_not_there_prints_nothing(home: Path, stub: Stub) -> None:
    stub.server.shutdown()
    stub.server.server_close()
    done = run(home, PERMISSION)
    assert done.returncode == 0 and done.stdout == "" and done.stderr == ""


def test_missing_files_a_server_error_and_garbage_all_print_nothing(home: Path, stub: Stub) -> None:
    stub.status = 500
    stub.says(decision="allow")  # even a body that says allow is not trusted on an error
    assert run(home, PERMISSION).stdout == ""
    stub.status = 200
    stub.reply = b"not json"
    assert run(home, PERMISSION).stdout == ""
    stub.says(decision="allow")
    assert run(home, None, raw="this is not json").stdout == ""
    assert run(home, ["a", "list"]).stdout == ""
    (home / ".cuttlefish" / "hook-token").unlink()
    assert run(home, PERMISSION).stdout == ""
    (home / ".cuttlefish" / "daemon.json").unlink()
    done = run(home, PERMISSION)
    assert done.returncode == 0 and done.stdout == ""


def test_a_daemon_that_is_too_slow_is_given_up_on_without_an_allow(home: Path, stub: Stub) -> None:
    stub.delay = 4
    stub.says(decision="allow")
    started = time.monotonic()
    done = run(home, PERMISSION, timeout="1")
    assert done.returncode == 0 and done.stdout == "" and time.monotonic() - started < 3.5


def test_only_the_fields_the_daemon_needs_are_sent_and_a_big_input_is_clipped(
    home: Path, stub: Stub
) -> None:
    run(
        home,
        {
            **PERMISSION,
            "tool_input": {"file_path": "/x", "content": "A" * 50_000},
            "prompt": "the user's whole prompt",
            "tool_response": {"stdout": "big"},
            "transcript_path": "/t.jsonl",
            "tool_use_id": "toolu_1",
        },
    )
    sent = stub.seen[0]["body"]
    assert set(sent) == {
        "hook_event_name",
        "tool_name",
        "tool_input",
        "transcript_path",
        "tool_use_id",
    }
    assert len(sent["tool_input"]["content"]) <= 4001 and sent["tool_input"]["file_path"] == "/x"
