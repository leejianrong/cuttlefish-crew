"""Unit: `FleetDaemon`'s own bookkeeping (ADR-0009) -- independent of a live
delegation. A genuinely in-flight team is simulated with a plain `asyncio.Task`
(no satay, no backend) so "already running" rejection is testable without needing
a real (or even a deliberately-failing) kopicode invocation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from cuttlefish.delegate.presets import DEFAULT_PRESETS
from cuttlefish.episodic.events import RequestRaised, TaskSubmitted, TeamResumed
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.fleet.daemon import FleetDaemon, FleetError, RunningTeam, _build_role_inputs
from cuttlefish.projects.store import PersistedRole, ProjectStore, RoleDefinition
from cuttlefish.requests import unresolved


def _daemon(tmp_path: Path) -> FleetDaemon:
    return FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))


async def _inject_running(daemon: FleetDaemon, project_id: str) -> asyncio.Task[None]:
    """A fake `RunningTeam` whose task never finishes on its own -- the caller
    cancels it once the test no longer needs it running."""

    async def _forever() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(_forever())
    daemon._running[project_id] = RunningTeam(
        team_id="fake-team", base_url="http://127.0.0.1:0", token="fake-token", task=task
    )
    return task


def test_build_role_inputs_threads_the_projects_declared_allow_to_every_role(
    tmp_path: Path,
) -> None:
    """The bug this closes: `FleetDaemon.start` used to build `RoleInput`s with no
    `allow` key at all, so `run_team` fell back to `DEFAULT_SHELL_ALLOWLIST`
    (no shell command allowed) for every daemon-started team, regardless of what
    the project itself declared (D1D2 live-usage findings)."""
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    project = daemon.projects.register(
        name="alpha",
        root=str(tmp_path / "alpha"),
        roles=(RoleDefinition(name="builder", persona="ships fast"),),
        allow=(("uv", "run", "pytest"), ("go", "test")),
    )

    role_inputs = _build_role_inputs(
        project, [{"name": "builder", "text": "add a test"}, {"name": "reviewer", "text": "look"}]
    )

    assert role_inputs[0]["text"] == "You are builder. ships fast\n\nadd a test"
    assert role_inputs[1]["text"] == "look"  # no registered persona -- runs as-is
    for role_input in role_inputs:
        assert role_input["allow"] == [["uv", "run", "pytest"], ["go", "test"]]


async def test_starting_an_already_running_project_raises_without_touching_it(
    tmp_path: Path,
) -> None:
    daemon = _daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    task = await _inject_running(daemon, project.id)

    with pytest.raises(FleetError):
        await daemon.start(project.id, [{"name": "builder", "text": "second attempt"}])

    running = daemon.running(project.id)
    assert running is not None
    assert running.team_id == "fake-team"  # the original, untouched by the rejected call

    task.cancel()


async def test_running_forgets_a_task_once_it_finishes(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    task = await _inject_running(daemon, project.id)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert daemon.running(project.id) is None
    assert daemon.is_running(project.id) is False


# -- resume_pending (ADR-0010/KAN-1703) -----------------------------------------
#
# These cover resume_pending's own skip conditions, none of which need a real
# satay run or kopicode -- see tests/integration/test_fleet_resume.py for the
# end-to-end "a crashed team actually gets driven to a terminal state again"
# proof, which does.


async def test_resume_pending_skips_a_project_that_never_started_a_team(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    assert await daemon.resume_pending() == []


async def test_resume_pending_skips_a_project_with_no_persisted_roles(tmp_path: Path) -> None:
    """A project whose `last_team_id` predates this fix (or was started by a
    plain `cuttlefish run-team`, which never persists roles at all) has no
    durable record of the `TeamInput` its last run used -- skipped, not resumed
    with a guessed-at input."""
    daemon = _daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    daemon.projects.record_team_started(project.id, "orphaned-team")  # roles default to ()
    assert await daemon.resume_pending() == []


async def test_resume_pending_skips_a_project_with_no_satay_data_dir_yet(tmp_path: Path) -> None:
    """`last_team_id`/`last_team_roles` persisted, but `<root>/.satay` was never
    actually created (e.g. the process died before `satay.control.run_app` ever
    opened) -- nothing to resume, not an error."""
    daemon = _daemon(tmp_path)
    root = tmp_path / "alpha"
    root.mkdir()
    project = daemon.projects.register(name="alpha", root=str(root))
    daemon.projects.record_team_started(
        project.id, "phantom-team", (PersistedRole(name="builder", text="do it"),)
    )
    assert await daemon.resume_pending() == []


async def test_resume_pending_skips_a_project_already_running(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    root = tmp_path / "alpha"
    root.mkdir()
    project = daemon.projects.register(name="alpha", root=str(root))
    daemon.projects.record_team_started(
        project.id, "already-running-team", (PersistedRole(name="builder", text="do it"),)
    )
    task = await _inject_running(daemon, project.id)

    assert await daemon.resume_pending() == []

    task.cancel()


# -- _mark_resumed (ADR-0010/KAN-1705) ------------------------------------------


def test_mark_resumed_journals_the_highest_existing_seq(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    root = tmp_path / "alpha"
    root.mkdir()
    project = daemon.projects.register(name="alpha", root=str(root))

    episodic_path = root / ".cuttlefish" / "episodic.db"
    store = EpisodicStore.open(episodic_path)
    store.append("team-1", TaskSubmitted(text="do it"))
    store.append("team-1", TaskSubmitted(text="do it more", role="builder"))
    store.close()

    daemon._mark_resumed(project, "team-1")

    store = EpisodicStore.open(episodic_path)
    events = list(store.read("team-1"))
    store.close()

    assert len(events) == 3
    assert isinstance(events[-1].payload, TeamResumed)
    assert events[-1].payload.resumed_from_seq == 2


def test_mark_resumed_on_an_empty_journal_resumes_from_seq_zero(tmp_path: Path) -> None:
    """A team whose crash landed before its very first journal write still gets a
    marker -- `resumed_from_seq=0` reads honestly as "nothing was ever recorded",
    not a missing/broken marker."""
    daemon = _daemon(tmp_path)
    root = tmp_path / "alpha"
    root.mkdir()
    project = daemon.projects.register(name="alpha", root=str(root))

    daemon._mark_resumed(project, "team-1")

    store = EpisodicStore.open(root / ".cuttlefish" / "episodic.db")
    events = list(store.read("team-1"))
    store.close()

    assert len(events) == 1
    assert isinstance(events[0].payload, TeamResumed)
    assert events[0].payload.resumed_from_seq == 0


def test_role_backend_is_threaded_into_inputs_and_survives_the_persisted_checkpoint(
    tmp_path: Path,
) -> None:
    """KAN-1809: a role's registered backend reaches its `RoleInput`, and a resume
    (`_persisted_roles_to_inputs`) rebuilds the identical input, backend included."""
    from cuttlefish.fleet.daemon import _persisted_roles_to_inputs, _to_persisted_roles

    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    project = daemon.projects.register(
        name="alpha",
        root=str(tmp_path / "alpha"),
        roles=(RoleDefinition(name="reviewer", backend="claude-code"),),
        backend="codex",
    )
    role_inputs = _build_role_inputs(
        project, [{"name": "builder", "text": "a"}, {"name": "reviewer", "text": "b"}]
    )
    assert "backend" not in role_inputs[0]  # falls through to the project's default
    assert role_inputs[1]["backend"] == "claude-code"
    assert _persisted_roles_to_inputs(_to_persisted_roles(role_inputs)) == role_inputs


def _project_with_mode(tmp_path: Path, mode: str):  # type: ignore[no-untyped-def]
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    return daemon.projects.register(
        name="alpha",
        root=str(tmp_path / "alpha"),
        mode=mode,
        roles=(
            RoleDefinition(name="builder"),
            RoleDefinition(name="reviewer", access="read-only"),
            RoleDefinition(name="tester", access="standard"),
        ),
    )


def test_a_standard_project_passes_no_access_so_replay_arguments_are_unchanged(
    tmp_path: Path,
) -> None:
    project = _project_with_mode(tmp_path, "standard")
    inputs = _build_role_inputs(
        project, [{"name": "builder", "text": "x"}, {"name": "tester", "text": "y"}]
    )
    assert all("access" not in role_input for role_input in inputs)


def test_an_auto_project_gives_every_role_auto_except_where_it_overrides(tmp_path: Path) -> None:
    project = _project_with_mode(tmp_path, "auto")
    inputs = _build_role_inputs(
        project,
        [
            {"name": "builder", "text": "x"},
            {"name": "reviewer", "text": "y"},
            {"name": "tester", "text": "z"},
            {"name": "unregistered", "text": "w"},
        ],
    )
    assert [role_input.get("access") for role_input in inputs] == [
        "auto",
        "read-only",
        None,  # an explicit standard override is the default, so nothing is passed
        "auto",
    ]


def test_default_presets_pass_nothing_and_a_custom_set_is_passed_to_every_role(
    tmp_path: Path,
) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    plain = daemon.projects.register(name="a", root=str(tmp_path / "a"))
    same = daemon.projects.register(
        name="b", root=str(tmp_path / "b"), presets=tuple(DEFAULT_PRESETS)
    )
    custom = daemon.projects.register(
        name="c", root=str(tmp_path / "c"), presets=("inspect", "containers")
    )
    roles = [{"name": "builder", "text": "x"}]
    assert "presets" not in _build_role_inputs(plain, roles)[0]
    assert "presets" not in _build_role_inputs(same, roles)[0]
    assert _build_role_inputs(custom, roles)[0]["presets"] == ["inspect", "containers"]


def test_the_environment_note_sits_between_the_persona_and_the_task(tmp_path: Path) -> None:
    from cuttlefish.fleet.daemon import _compose_role_text
    from cuttlefish.projects.store import RoleDefinition

    role = RoleDefinition(name="builder", persona="Build things.")

    assert _compose_role_text(role, "do it", "Environment: x") == (
        "You are builder. Build things.\n\nEnvironment: x\n\ndo it"
    )
    assert _compose_role_text(None, "do it", "Environment: x") == "Environment: x\n\ndo it"
    assert _compose_role_text(role, "do it") == "You are builder. Build things.\n\ndo it"


# -- a restart keeps a Stuck card for a role that is resumed (ADR-0029, ADR-0030) --------


def _raised(request_id: str, kind: str) -> RequestRaised:
    return RequestRaised(
        request_id=request_id,
        kind=kind,  # type: ignore[arg-type]
        title="t",
        detail="d",
        why="w",
        answers=[] if kind == "blocked" else ["deny"],
        expires_at="",
        role="builder",
    )


def _journal_two_requests(daemon: FleetDaemon, tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "alpha"
    root.mkdir()
    project = daemon.projects.register(name="alpha", root=str(root))
    daemon.projects.record_team_started(
        project.id, "t-1", (PersistedRole(name="builder", text="do it"),)
    )
    store = EpisodicStore.open(root / ".cuttlefish" / "episodic.db")
    store.append("t-1", _raised("stuck", "blocked"))
    store.append("t-1", _raised("held", "permission"))
    store.close()
    return root, "t-1"


def _unresolved_ids(root: Path) -> set[str]:
    store = EpisodicStore.open(root / ".cuttlefish" / "episodic.db")
    try:
        return {r.request_id for r in unresolved(store.read("t-1"))}
    finally:
        store.close()


def test_the_sweep_abandons_every_pending_request_by_default(tmp_path: Path) -> None:
    daemon = _daemon(tmp_path)
    root, _ = _journal_two_requests(daemon, tmp_path)
    assert daemon.sweep_abandoned() == 2
    assert _unresolved_ids(root) == set()


def test_the_sweep_keeps_a_blocked_request_of_a_team_about_to_resume(tmp_path: Path) -> None:
    """A held command's agent process is gone; a Stuck card has nothing waiting on it."""
    daemon = _daemon(tmp_path)
    root, team = _journal_two_requests(daemon, tmp_path)
    assert daemon.sweep_abandoned(keep_blocked={team}) == 1
    assert _unresolved_ids(root) == {"stuck"}
