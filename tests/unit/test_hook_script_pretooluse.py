"""CUT-183: the hook script turns the daemon's refusal of a tool call into Claude Code's own
`PreToolUse` deny, and stays silent for anything that is not a refusal."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cuttlefish.sessions import hook_script


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    event: str,
    answer: dict[str, Any],
) -> str:
    home = tmp_path / ".cuttlefish"
    home.mkdir(exist_ok=True)
    (home / "daemon.json").write_text(json.dumps({"url": "http://127.0.0.1:1"}))
    (home / "hook-token").write_text("t")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"hook_event_name": event})))
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(json.dumps(answer).encode()),
    )
    assert hook_script.main(["--project", "p"]) == 0
    return capsys.readouterr().out


def test_a_refused_pretooluse_prints_a_deny(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = json.loads(
        _run(monkeypatch, tmp_path, capsys, "PreToolUse", {"decision": "deny", "message": "no"})
    )["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and out["permissionDecisionReason"] == "no"


def test_pretooluse_never_prints_an_allow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, tmp_path, capsys, "PreToolUse", {"decision": "allow"}) == ""
    assert _run(monkeypatch, tmp_path, capsys, "PreToolUse", {}) == ""


# CUT-277: a held PermissionRequest outlives a daemon restart by asking again.


def _retry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    event: str,
    outcomes: list[Any],
) -> tuple[str, int]:
    """Run with `urlopen` failing or answering per `outcomes`; returns (stdout, calls)."""
    home = tmp_path / ".cuttlefish"
    home.mkdir(exist_ok=True)
    (home / "daemon.json").write_text(json.dumps({"url": "http://127.0.0.1:1"}))
    (home / "hook-token").write_text("t")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"hook_event_name": event})))
    ticks = iter(range(0, 1_000_000))
    _fake_time(monkeypatch, lambda: float(next(ticks)))
    calls: list[int] = []

    def fake(*_a: object, **_k: object) -> io.BytesIO:
        outcome = outcomes[min(len(calls), len(outcomes) - 1)]
        calls.append(1)
        if isinstance(outcome, BaseException):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert hook_script.main(["--project", "p", "--timeout", "600"]) == 0
    return capsys.readouterr().out, len(calls)


def test_a_dropped_permission_hold_asks_the_restarted_daemon_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    refused = urllib.error.URLError(ConnectionRefusedError())
    out, calls = _retry_run(
        monkeypatch,
        tmp_path,
        capsys,
        "PermissionRequest",
        [ConnectionResetError(), refused, {"decision": "allow"}],
    )
    assert calls == 3
    assert json.loads(out)["hookSpecificOutput"]["decision"] == {"behavior": "allow"}


def test_a_daemon_that_never_returns_ends_in_silence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, calls = _retry_run(
        monkeypatch, tmp_path, capsys, "PermissionRequest", [ConnectionResetError()]
    )
    assert out == ""
    assert 1 < calls < 40  # it tried again, but only for the retry window


def test_a_refused_first_connection_does_not_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    refused = urllib.error.URLError(ConnectionRefusedError())
    out, calls = _retry_run(monkeypatch, tmp_path, capsys, "PermissionRequest", [refused])
    assert (out, calls) == ("", 1)


def test_a_quick_event_never_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, calls = _retry_run(monkeypatch, tmp_path, capsys, "PreToolUse", [ConnectionResetError()])
    assert (out, calls) == ("", 1)


def _fake_time(monkeypatch: pytest.MonkeyPatch, monotonic: Callable[[], float]) -> None:
    """The script's own clock, so a retry window runs instantly and pytest's clock is untouched."""
    stub = SimpleNamespace(sleep=lambda _s: None, monotonic=monotonic)
    monkeypatch.setattr(hook_script, "time", stub)


def test_a_shutdown_release_asks_the_restarted_daemon_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    refused = urllib.error.URLError(ConnectionRefusedError())
    out, calls = _retry_run(
        monkeypatch,
        tmp_path,
        capsys,
        "PermissionRequest",
        [{"retry": True}, refused, {"decision": "deny", "message": "no"}],
    )
    assert calls == 3
    assert json.loads(out)["hookSpecificOutput"]["decision"]["behavior"] == "deny"
