"""CUT-183: the hook script turns the daemon's refusal of a tool call into Claude Code's own
`PreToolUse` deny, and stays silent for anything that is not a refusal."""

from __future__ import annotations

import io
import json
from pathlib import Path
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
        hook_script.urllib.request,
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
