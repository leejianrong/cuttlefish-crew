"""Integration: a daemon-launched team asks a person about a command off the list (ADR-0028).

A real team under `FleetDaemon` (real satay, real `prepare_run`), with the scripted fake
`kopicode serve` standing in for the binary: the request is raised through the daemon's own
broker, attributed to the role that dispatched it, answered by `FleetDaemon.answer_request`,
and journaled to the project's own episodic store.
"""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from cuttlefish import runtime
from cuttlefish.episodic.events import (
    DelegationCompleted,
    RequestRaised,
    RequestResolved,
)
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.projects.store import ProjectStore

FAKE = Path(__file__).parents[1] / "unit" / "delegate" / "fake_kopicode_serve.py"
SH = "/bin/sh -c "
LINE = "docker compose up -d postgres"  # nothing in the default presets approves this


@pytest.fixture(autouse=True)
def _reset_runtime() -> Any:
    yield
    runtime.reset()


def _fake_kopicode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: list[Any]) -> None:
    (tmp_path / "scenario.json").write_text(json.dumps(scenario))
    monkeypatch.setenv("FAKE_KOPICODE_SCENARIO", str(tmp_path / "scenario.json"))
    monkeypatch.setenv("FAKE_KOPICODE_LOG", str(tmp_path / "sent.jsonl"))
    binary = tmp_path / "kopicode"
    binary.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_BIN", str(binary))
    monkeypatch.setenv("CUTTLEFISH_LLM_PROVIDER", "replay")
    monkeypatch.delenv("CUTTLEFISH_SECRETS_KEY", raising=False)


def _scenario() -> list[Any]:
    result = {
        "jsonrpc": "2.0",
        "id": "$start_id",
        "result": {"session": "$session", "record": "/r", "stop": "completed", "exit_code": 0},
    }
    ended = {
        "jsonrpc": "2.0",
        "method": "session.event",
        "params": {
            "session": "$session",
            "event": {"kind": "session_ended", "reason": "completed", "exit_code": 0},
        },
    }
    return [
        {"start": True},
        {
            "consent": {"id": "c-1", "kind": "run_shell", "detail": SH + LINE},
            "wait": 20,
        },
        {"emit": result},
        {"close": [ended]},
        {"eof": []},
    ]


def _journal(project_root: str, team_id: str) -> list[Any]:
    store = EpisodicStore.open(Path(project_root) / ".cuttlefish" / "episodic.db")
    try:
        return [e.payload for e in store.read(team_id)]
    finally:
        store.close()


async def _until(condition: Any, seconds: float = 30) -> None:
    for _ in range(int(seconds / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("timed out")


async def test_a_team_asks_a_person_and_the_answer_reaches_the_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch, _scenario())
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"), request_window_s=60)
    root = tmp_path / "alpha"
    root.mkdir()
    project = daemon.projects.register(name="alpha", root=str(root))
    team_id = await daemon.start(project.id, [{"name": "builder", "text": "bring the db up"}])
    try:
        await _until(lambda: bool(daemon.requests.pending(project.id)))
        request = daemon.requests.pending(project.id)[0]
        assert request.team_id == team_id
        assert request.record.role == "builder"  # attributed through satay.gather
        assert request.record.detail == LINE and request.record.backend == "kopicode"

        outcome = daemon.answer_request(project.id, request.id, "allow_once")
        assert outcome.resolution == "allowed_once"
        await _until(
            lambda: any(isinstance(p, DelegationCompleted) for p in _journal(project.root, team_id))
        )
    finally:
        if daemon.is_running(project.id):
            await daemon.stop(project.id)

    payloads = _journal(project.root, team_id)
    raised = [p for p in payloads if isinstance(p, RequestRaised)]
    resolved = [p for p in payloads if isinstance(p, RequestResolved)]
    assert [r.resolution for r in resolved] == ["allowed_once"]
    assert resolved[0].request_id == raised[0].request_id
    log = (tmp_path / "sent.jsonl").read_text()
    assert '"result": {"answer": "allow"}' in log


async def test_stopping_the_team_while_asked_cancels_the_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kopicode(tmp_path, monkeypatch, _scenario())
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"), request_window_s=60)
    root = tmp_path / "alpha"
    root.mkdir()
    project = daemon.projects.register(name="alpha", root=str(root))
    team_id = await daemon.start(project.id, [{"name": "builder", "text": "bring the db up"}])
    await _until(lambda: bool(daemon.requests.pending(project.id)))
    await daemon.stop(project.id)  # satay's cancel waits for the round, so this releases the agent
    assert not daemon.requests.pending(project.id)
    await _until(lambda: not daemon.is_running(project.id))
    resolved = [p for p in _journal(project.root, team_id) if isinstance(p, RequestResolved)]
    assert [r.resolution for r in resolved] == ["cancelled"]
    assert '"result": {"answer": "deny"}' in (tmp_path / "sent.jsonl").read_text()


def test_a_restart_abandons_what_a_dead_daemon_left_pending(tmp_path: Path) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    root = tmp_path / "alpha"
    root.mkdir()
    project = daemon.projects.register(name="alpha", root=str(root))
    daemon.projects.record_team_started(project.id, "t-dead", ())
    store = EpisodicStore.open(root / ".cuttlefish" / "episodic.db")
    for rid in ("r1", "r2"):
        store.append(
            "t-dead",
            RequestRaised(
                request_id=rid,
                kind="permission",
                title="t",
                detail=LINE,
                why="w",
                answers=["allow_once", "deny"],
                expires_at="2026-10-06T10:00:00+00:00",
            ),
        )
    store.append("t-dead", RequestResolved(request_id="r1", resolution="denied", by="person"))
    store.close()

    assert daemon.sweep_abandoned() == 1  # only r2 was still pending
    resolved = {
        p.request_id: p.resolution
        for p in _journal(project.root, "t-dead")
        if isinstance(p, RequestResolved)
    }
    assert resolved == {"r1": "denied", "r2": "abandoned"}
    assert daemon.sweep_abandoned() == 0  # idempotent
