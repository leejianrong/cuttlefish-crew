"""Integration: `FleetDaemon.resume_pending` (ADR-0010/KAN-1703).

`tests/integration/test_crash_recovery.py` already proves satay's own
`RunController.result()` resumes a non-terminal run correctly when called again
with the same ``run_id`` -- this file proves the *daemon* actually reaches that
primitive the same way after a restart, by simulating one: crash a team's run
directly against a project's real ``.satay`` data dir, then hand a *freshly
constructed* `FleetDaemon` (the same shape a restarted `cuttlefish serve` process
would build) nothing but the `ProjectStore` row `record_team_started` persisted,
and check it finds and completes the interrupted team on its own.

No mock kopicode (docs/PLAN.md "Testing approach") -- both credentials are
deleted so the delegation fails fast on a missing credential, a real, recorded,
no-cost terminal outcome, the same trick `test_crash_recovery.py` already uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import satay
from satay.config import db_path as satay_db_path
from satay.journal.store import SQLiteStore
from satay.testing.faults import FaultInjector, SimulatedCrash

from cuttlefish import runtime
from cuttlefish.config import prepare_run
from cuttlefish.episodic.events import TeamResumed
from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.projects.store import PersistedRole, ProjectStore
from cuttlefish.team import RoleInput, TeamInput, run_team


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


@pytest.mark.requires_kopicode
async def test_resume_pending_redrives_a_team_a_simulated_daemon_restart_left_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CUTTLEFISH_LLM_PROVIDER", "replay")
    monkeypatch.delenv("CUTTLEFISH_SECRETS_KEY", raising=False)

    root = tmp_path / "alpha"
    root.mkdir()
    projects_db = tmp_path / "projects.db"
    project_store = ProjectStore.open(projects_db)
    project = project_store.register(name="alpha", root=str(root))

    team_id = "resume-test-team"
    role_inputs: list[RoleInput] = [{"name": "builder", "text": "add a .gitignore entry"}]
    workflow_input: TeamInput = {
        "team_id": team_id,
        "root": str(root),
        "project": project.secrets_scope,
        "roles": role_inputs,
        "steerable": False,
    }

    # -- Phase 1: start the team directly against the project's real .satay data
    # dir, then crash it right after its first satay-level task completion --
    # the same "first TaskCompleted, deterministically the first of that type in
    # a fresh run" point test_crash_recovery.py's own full-workflow test crashes
    # at, here for run_team instead of run_task.
    prepared = prepare_run(project=project.secrets_scope, secret_names=[], base_dir=root)
    runtime.configure(prepared.as_runtime())
    database = satay_db_path(root / ".satay")
    database.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStore.open(database)
    injector = FaultInjector()
    injector.crash_after("TaskCompleted")
    handle = satay.start(run_team, workflow_input, run_id=team_id, store=store, injector=injector)
    with pytest.raises(SimulatedCrash):
        await handle.result()
    store.close()
    prepared.close()
    runtime.reset()

    # -- Phase 2: persist exactly what `FleetDaemon.start` would have (this is
    # the fix KAN-1703 adds -- a plain `satay.start` crash, with nothing
    # persisted to `ProjectStore`, is exactly the pre-fix "orphaned forever"
    # case `docs/adr/0010` names).
    project_store.record_team_started(
        project.id,
        team_id,
        tuple(PersistedRole(name=r["name"], text=r["text"], allow=()) for r in role_inputs),
    )

    # -- Phase 3: a *fresh* FleetDaemon (the shape a restarted `cuttlefish serve`
    # builds) finds and resumes it, with no other input than the ProjectStore row.
    daemon = FleetDaemon(project_store)
    attempts = await daemon.resume_pending()
    assert len(attempts) == 1
    assert attempts[0].project_id == project.id
    assert attempts[0].team_id == team_id
    assert attempts[0].error is None

    running = daemon.running(project.id)
    assert running is not None
    await running.task  # let the resumed team actually finish

    events = daemon.events(project.id)
    kinds = [type(event.payload).__name__ for event in events]
    # Exactly one TaskSubmitted -- the interrupted-and-resumed append was reused
    # from the journal, not written a second time.
    assert kinds.count("TaskSubmitted") == 1
    assert kinds[-1] == "TaskFailed"  # missing credential -> a real, fast, no-cost failure

    # ADR-0010/KAN-1705: an explicit, distinct marker -- not silently indistinguishable
    # from ordinary progress -- right at the gap the crash actually left (only
    # TaskSubmitted, seq 1, was journaled before the simulated crash).
    assert kinds.count("TeamResumed") == 1
    resumed_event = events[kinds.index("TeamResumed")]
    assert isinstance(resumed_event.payload, TeamResumed)
    assert resumed_event.payload.resumed_from_seq == 1

    project_store.close()
