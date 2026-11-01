"""Integration: a daemon-launched team runs through the ``acp`` backend (CUT-260).

A real team under `FleetDaemon` (real satay, real `prepare_run`) with the fake ACP agent
(`tests/fixtures/fake_acp_agent.py`) as ``CUTTLEFISH_ACP_AGENT``. No model is called: this is the
protocol path, end to end, not a real agent.
"""

from __future__ import annotations

import asyncio
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

from cuttlefish import runtime
from cuttlefish.config import ConfigError, prepare_run
from cuttlefish.episodic.events import (
    ConsentDecided,
    DelegationCompleted,
    DelegationRefused,
    DelegationStarted,
    RequestResolved,
)
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.projects.store import ProjectStore

FAKE = Path(__file__).parents[1] / "fixtures" / "fake_acp_agent.py"
LINE = "docker compose up -d postgres"  # nothing in the default presets approves this


@pytest.fixture(autouse=True)
def _reset_runtime() -> Any:
    yield
    runtime.reset()


@pytest.fixture(autouse=True)
def _acp_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTTLEFISH_ACP_AGENT", shlex.join([sys.executable, str(FAKE)]))
    monkeypatch.delenv("CUTTLEFISH_SECRETS_KEY", raising=False)


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


async def test_a_named_acp_agent_completes_a_task_and_its_command_asks_a_person(
    tmp_path: Path,
) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"), request_window_s=60)
    root = tmp_path / "alpha"
    root.mkdir()
    project = daemon.projects.register(name="alpha", root=str(root), backend="acp")
    team_id = await daemon.start(project.id, [{"name": "builder", "text": f"run {LINE}"}])
    try:
        await _until(lambda: bool(daemon.requests.pending(project.id)))
        request = daemon.requests.pending(project.id)[0]
        assert request.record.role == "builder" and request.record.backend == "acp"
        assert request.record.detail == LINE
        outcome = daemon.answer_request(project.id, request.id, "allow_once")
        assert outcome.resolution == "allowed_once"
        await _until(
            lambda: any(isinstance(p, DelegationCompleted) for p in _journal(project.root, team_id))
        )
    finally:
        if daemon.is_running(project.id):
            await daemon.stop(project.id)
    payloads = _journal(project.root, team_id)
    started = [p for p in payloads if isinstance(p, DelegationStarted)]
    assert started and started[0].backend == "acp"
    assert [p.resolution for p in payloads if isinstance(p, RequestResolved)] == ["allowed_once"]


async def test_a_blocked_command_is_refused_in_auto_without_asking_a_person(
    tmp_path: Path,
) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"), request_window_s=60)
    root = tmp_path / "alpha"
    root.mkdir()
    project = daemon.projects.register(name="alpha", root=str(root), backend="acp", mode="auto")
    team_id = await daemon.start(project.id, [{"name": "builder", "text": "run sudo rm -rf /"}])
    try:
        await _until(
            lambda: any(isinstance(p, DelegationRefused) for p in _journal(project.root, team_id))
        )
    finally:
        if daemon.is_running(project.id):
            await daemon.stop(project.id)
    decided = [p for p in _journal(project.root, team_id) if isinstance(p, ConsentDecided)]
    assert decided[0].answer == "deny" and decided[0].rule.startswith("never_allowed")
    assert not daemon.requests.pending(project.id)


def test_the_acp_backend_needs_a_command_and_it_must_be_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CUTTLEFISH_ACP_AGENT")
    with pytest.raises(ConfigError, match="CUTTLEFISH_ACP_AGENT"):
        prepare_run(project="p", secret_names=[], base_dir=tmp_path, agent_backend="acp")
    monkeypatch.setenv("CUTTLEFISH_ACP_AGENT", "no-such-acp-agent-binary --acp")
    with pytest.raises(ConfigError, match="not on PATH"):
        prepare_run(project="p", secret_names=[], base_dir=tmp_path, agent_backend="acp")
