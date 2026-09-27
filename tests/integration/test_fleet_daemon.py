"""Integration: `FleetDaemon.start` against real `cuttlefish.config.prepare_run`
resolution and a real (deliberately missing) kopicode binary -- the same
no-mock, fast-and-deterministic discipline `test_team.py`/`test_delegate.py` already
hold, a `ConfigError` (surfaced as `FleetError`) being the real, fast failure mode
this exercises rather than something faked.

`start()` going through the real config-resolution path is itself the thing worth
covering here: `FleetDaemon` reuses `cuttlefish.config.prepare_run` (ADR-0009)
rather than a second, daemon-only config path, so a config mistake (missing
binary, bad `CUTTLEFISH_SECRETS_KEY`, ...) fails a daemon-launched team exactly the
way it already fails a CLI-launched one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish.fleet.daemon import FleetDaemon, FleetError
from cuttlefish.projects.store import ProjectStore, RoleDefinition


@pytest.fixture(autouse=True)
def _missing_kopicode_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_BIN", "cuttlefish-test-missing-kopicode-binary")
    monkeypatch.setenv("CUTTLEFISH_LLM_PROVIDER", "replay")
    monkeypatch.delenv("CUTTLEFISH_SECRETS_KEY", raising=False)


def _new_daemon(tmp_path: Path) -> FleetDaemon:
    return FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))


async def test_start_surfaces_a_config_error_as_fleeterror_not_a_hang(tmp_path: Path) -> None:
    """The missing-binary failure happens inside `config.prepare_run`, before
    `satay.control.run_app` ever opens -- `start()` must still resolve (as a
    `FleetError`), not hang forever waiting on its own readiness signal."""
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    Path(project.root).mkdir()

    with pytest.raises(FleetError, match="failed to start"):
        await daemon.start(project.id, [{"name": "builder", "text": "do it"}])

    # The failure was reported synchronously to the caller -- nothing left running.
    assert daemon.running(project.id) is None


async def test_two_projects_fail_to_start_independently_without_blocking_each_other(
    tmp_path: Path,
) -> None:
    """Two `FleetDaemon.start()` calls for two *different* projects run as genuinely
    separate `asyncio.create_task`s -- neither's config resolution blocks the
    other's, the concrete property the `cuttlefish.runtime` `ContextVar` fix (Q49)
    exists to make safe once a *successful* start is in the picture too."""
    import asyncio

    daemon = _new_daemon(tmp_path)
    project_a = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    project_b = daemon.projects.register(name="beta", root=str(tmp_path / "beta"))
    Path(project_a.root).mkdir()
    Path(project_b.root).mkdir()

    results = await asyncio.gather(
        daemon.start(project_a.id, [{"name": "builder", "text": "do alpha work"}]),
        daemon.start(project_b.id, [{"name": "builder", "text": "do beta work"}]),
        return_exceptions=True,
    )
    assert all(isinstance(result, FleetError) for result in results)


def test_status_before_any_start_is_queued_for_every_registered_role(tmp_path: Path) -> None:
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(
        name="alpha",
        root=str(tmp_path / "alpha"),
        roles=(RoleDefinition(name="builder"), RoleDefinition(name="reviewer")),
    )
    assert daemon.status(project.id) == {"builder": "queued", "reviewer": "queued"}


def test_status_for_a_project_with_no_declared_roles_and_no_team_yet_is_empty(
    tmp_path: Path,
) -> None:
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    assert daemon.status(project.id) == {}


async def test_steer_with_no_running_team_raises(tmp_path: Path) -> None:
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    with pytest.raises(FleetError):
        await daemon.steer(project.id, "builder", "hello")


async def test_approve_with_no_running_team_raises(tmp_path: Path) -> None:
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    with pytest.raises(FleetError):
        await daemon.approve(project.id, "builder", approved=True)


async def test_stop_with_no_running_team_raises(tmp_path: Path) -> None:
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    with pytest.raises(FleetError):
        await daemon.stop(project.id)
