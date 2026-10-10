"""Integration: a team started by the daemon reaches the model API through the credential broker,
and its agent never holds the real key (ADR-0031). Claude Code is the scripted fake from the unit
tests."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from fake_upstream import Upstream

from cuttlefish import runtime
from cuttlefish.fleet.daemon import CREDENTIAL_BROKER_ENV, FleetDaemon
from cuttlefish.projects.store import ProjectStore

FAKE = Path(__file__).parents[1] / "unit" / "delegate" / "fake_claude_stream.py"
REAL = "canary-real-anthropic-key"


@pytest.fixture(autouse=True)
def _reset_runtime() -> Any:
    yield
    runtime.reset()


def _fake_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upstream: Upstream, *, steps: list[Any]
) -> Path:
    scenario, log = tmp_path / "scenario.json", tmp_path / "claude.log"
    scenario.write_text(json.dumps({"steps": steps}))
    log.touch()
    binary = tmp_path / "claude"
    binary.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} {scenario} {log} "$@"\n')
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CUTTLEFISH_CLAUDE_CODE_BIN", str(binary))
    monkeypatch.setenv("CUTTLEFISH_LLM_PROVIDER", "replay")
    monkeypatch.setenv("ANTHROPIC_API_KEY", REAL)  # the daemon's own, as `cuttlefish serve` has it
    monkeypatch.setenv("ANTHROPIC_BASE_URL", upstream.base)
    return log


async def _run_team(daemon: FleetDaemon, tmp_path: Path) -> None:
    (tmp_path / "demo").mkdir()
    project = daemon.projects.register(
        name="demo", root=str(tmp_path / "demo"), backend="claude-code"
    )
    await daemon.start(project.id, [{"name": "builder", "text": "go"}])
    for _ in range(600):
        if not daemon.is_running(project.id):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the team never finished")


def _claude_log(log: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


async def test_with_the_broker_on_the_agent_holds_a_token_and_the_call_carries_the_real_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upstream: Upstream
) -> None:
    log = _fake_claude(tmp_path, monkeypatch, upstream, steps=[{"api_call": True}])
    monkeypatch.setenv(CREDENTIAL_BROKER_ENV, "1")
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"), secrets_db=tmp_path / "s.db")
    try:
        await _run_team(daemon, tmp_path)
        lines = _claude_log(log)
        env = next(m["env"] for m in lines if "env" in m)
        assert env["ANTHROPIC_API_KEY"].startswith("cfb_")
        assert env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:")
        assert REAL not in log.read_text()  # nothing the agent could see holds the key
        assert {"api_status": 200} in lines
        (seen,) = upstream.seen
        assert seen["path"] == "/api/v1/messages"
        assert seen["headers"]["x-api-key"] == REAL
        assert daemon._broker is not None and daemon._broker.active() == 0  # the lease closed
    finally:
        await daemon.close()
    assert daemon._broker is None


async def test_with_the_broker_off_the_agent_gets_its_key_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upstream: Upstream
) -> None:
    log = _fake_claude(tmp_path, monkeypatch, upstream, steps=[])
    monkeypatch.delenv(CREDENTIAL_BROKER_ENV, raising=False)
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"), secrets_db=tmp_path / "s.db")
    assert daemon.broker_enabled() is False
    await _run_team(daemon, tmp_path)
    env = next(m["env"] for m in _claude_log(log) if "env" in m)
    assert env["ANTHROPIC_API_KEY"] == REAL
    assert env["ANTHROPIC_BASE_URL"] == upstream.base  # the daemon's own, passed through
    assert daemon._broker is None
