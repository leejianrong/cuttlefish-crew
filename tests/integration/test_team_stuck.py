"""Integration: a role stopped for failing on its environment waits for a person (ADR-0029).

A steerable team normally gives a finished round a few seconds' grace for a steer message and
then ends the role. A stuck role's Needs-you card says "fix it, then steer", so that wait has no
timeout; only a steer (or a stop) moves it on. Uses a stub backend, so it needs no kopicode.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import satay
from satay.journal.store import SQLiteStore

from cuttlefish import runtime
from cuttlefish.agents.outcome import DelegationOutcome
from cuttlefish.episodic.events import DelegationStarted, SteeringMessage
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.llm.replay import ReplayLlmProvider
from cuttlefish.steering import steering_key
from cuttlefish.team import run_team


class _Backend:
    NAME = "kopicode"
    CREDENTIAL_ENV_VARS: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.rounds = 0

    async def delegate(self, **_: object) -> DelegationOutcome:
        self.rounds += 1
        if self.rounds == 1:
            return DelegationOutcome(
                kind="failed",
                summary="stopped",
                reason="stopped after 5 shell commands in a row failed",
                failure_kind="environment_stuck",
            )
        return DelegationOutcome(kind="completed", summary="done")


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


async def test_a_stuck_role_outlasts_the_steering_grace_and_resumes_on_a_steer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _Backend()
    monkeypatch.setattr("cuttlefish.tasks.delegate.resolve_backend", lambda *a, **k: backend)
    episodic_store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=episodic_store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode",
        )
    )
    team_id = "stuck-team"
    async with satay.run_app(store=SQLiteStore.open(":memory:")) as store:
        handle = satay.start(
            run_team,
            {
                "team_id": team_id,
                "root": str(tmp_path),
                "roles": [{"name": "builder", "text": "do it"}],
                "steerable": True,
                "steering_grace": 0.2,
            },
            run_id=team_id,
            store=store,
        )
        running = asyncio.create_task(handle.result())  # a run only advances while awaited
        await asyncio.sleep(2.0)  # ten times the grace
        assert backend.rounds == 1
        assert not running.done()
        await satay.send_event(
            SteeringMessage(text="numpy is installed now", role="builder"),
            key=steering_key(team_id, "builder"),
            store=store,
        )
        result = await running

    assert result["status"] == "completed"
    starts = [e for e in episodic_store.read(team_id) if isinstance(e.payload, DelegationStarted)]
    assert len(starts) == 2
    episodic_store.close()
