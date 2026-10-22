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

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cuttlefish.episodic.events import (
    DelegationCompleted,
    DelegationRefused,
    SteeringMessage,
    SteeringQueued,
    StopRequested,
)
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.fleet.daemon import FleetDaemon, FleetError
from cuttlefish.projects.store import ProjectStore, RoleDefinition
from cuttlefish.steering import SteeringDeliveryError


@pytest.fixture(autouse=True)
def _missing_kopicode_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_BIN", "cuttlefish-test-missing-kopicode-binary")
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


async def test_a_failed_start_is_logged_with_its_cause(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The dashboard only says "check the daemon's log" -- so the log must name why."""
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    Path(project.root).mkdir()

    with (
        caplog.at_level(logging.ERROR, logger="cuttlefish.fleet.daemon"),
        pytest.raises(FleetError),
    ):
        await daemon.start(project.id, [{"name": "builder", "text": "do it"}])

    record = next(r for r in caplog.records if "failed to start" in r.getMessage())
    assert record.exc_info is not None
    assert "cuttlefish-test-missing-kopicode-binary" in str(record.exc_info[1])


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


def test_usage_before_any_start_is_zero_for_every_registered_role(tmp_path: Path) -> None:
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(
        name="alpha",
        root=str(tmp_path / "alpha"),
        roles=(RoleDefinition(name="builder"), RoleDefinition(name="reviewer")),
    )
    usage = daemon.usage(project.id)
    assert usage["builder"].tokens == 0
    assert usage["builder"].cost_usd is None  # no backend reported a dollar figure
    assert usage["reviewer"].tokens == 0


def test_usage_sums_a_roles_own_journaled_delegation_outcomes(tmp_path: Path) -> None:
    """`usage` reads the identical journal `status` already reads (KAN-1712/
    ADR-0017) -- proven here against a hand-journaled record, the same
    `EpisodicStore.open(<root>/.cuttlefish/episodic.db)` shape
    `tests/unit/fleet/test_daemon.py`'s own resume tests already use."""
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(
        name="alpha",
        root=str(tmp_path / "alpha"),
        roles=(RoleDefinition(name="builder"),),
    )
    daemon.projects.record_team_started(project.id, "team-1")

    episodic_path = Path(project.root) / ".cuttlefish" / "episodic.db"
    store = EpisodicStore.open(episodic_path)
    store.append(
        "team-1", DelegationCompleted(summary="ok", tokens=100, cost_usd=0.01, role="builder")
    )
    store.append(
        "team-1", DelegationRefused(reason="denied", tokens=10, cost_usd=None, role="builder")
    )
    store.close()

    usage = daemon.usage(project.id)
    assert usage["builder"].tokens == 110
    assert usage["builder"].cost_usd == 0.01


async def test_steer_with_no_running_team_raises(tmp_path: Path) -> None:
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    with pytest.raises(FleetError):
        await daemon.steer(project.id, "builder", "hello")


async def test_a_delivered_steer_is_journaled_at_once_as_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The message is journaled as applied only at the round boundary; the send is recorded now."""
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    store = EpisodicStore.open(tmp_path / "episodic.db")
    daemon._team_stores["team-1"] = store
    running = SimpleNamespace(team_id="team-1", base_url="http://x", token="t")
    monkeypatch.setattr(daemon, "running", lambda _project_id: running)
    delivered: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "cuttlefish.fleet.daemon.send_steering_message", lambda **kwargs: delivered.append(kwargs)
    )

    await daemon.steer(project.id, "builder", "use tabs")

    assert delivered[0]["text"] == "use tabs"
    queued = [e.payload for e in store.read("team-1") if isinstance(e.payload, SteeringQueued)]
    assert queued == [SteeringQueued(text="use tabs", role="builder")]
    store.close()


async def test_a_steer_into_a_running_codex_turn_is_applied_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0033: no queued message and no round boundary; both journal lines are written now."""
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    store = EpisodicStore.open(tmp_path / "episodic.db")
    daemon._team_stores["team-1"] = store
    running = SimpleNamespace(team_id="team-1", base_url="http://x", token="t")
    monkeypatch.setattr(daemon, "running", lambda _project_id: running)
    taken: list[tuple[str, str, str]] = []

    async def live(root: str, role: str, text: str) -> bool:
        taken.append((root, role, text))
        return True

    monkeypatch.setattr("cuttlefish.fleet.daemon.codex_live.steer", live)

    def never(**_kwargs: Any) -> None:
        raise AssertionError("a live steer must not also be queued")

    monkeypatch.setattr("cuttlefish.fleet.daemon.send_steering_message", never)

    await daemon.steer(project.id, "builder", "use tabs")

    assert taken == [(project.root, "builder", "use tabs")]
    payloads = [e.payload for e in store.read("team-1")]
    assert payloads == [
        SteeringQueued(text="use tabs", role="builder"),
        SteeringMessage(text="use tabs", role="builder"),
    ]
    store.close()


async def test_a_steer_that_fails_to_deliver_is_not_journaled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    store = EpisodicStore.open(tmp_path / "episodic.db")
    daemon._team_stores["team-1"] = store
    running = SimpleNamespace(team_id="team-1", base_url="http://x", token="t")
    monkeypatch.setattr(daemon, "running", lambda _project_id: running)

    def refuse(**_kwargs: Any) -> None:
        raise SteeringDeliveryError("no such task")

    monkeypatch.setattr("cuttlefish.fleet.daemon.send_steering_message", refuse)
    with pytest.raises(FleetError):
        await daemon.steer(project.id, "builder", "use tabs")
    assert list(store.read("team-1")) == []
    store.close()


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


async def test_stop_journals_that_it_was_asked_once_the_cancel_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The team workflow reads this at the round boundary to drop a queued steer (CUT-107)."""
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    store = EpisodicStore.open(tmp_path / "episodic.db")
    daemon._team_stores["team-1"] = store
    running = SimpleNamespace(team_id="team-1", base_url="http://x", token="t")
    monkeypatch.setattr(daemon, "running", lambda _project_id: running)
    monkeypatch.setattr("cuttlefish.fleet.daemon.cancel_run", lambda **_kwargs: None)

    await daemon.stop(project.id)

    assert [e.payload for e in store.read("team-1")] == [StopRequested()]
    store.close()


async def test_stop_interrupts_a_running_codex_turn_in_that_projects_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CUT-166: Stop ends a Codex turn with turn/interrupt, not at the round boundary."""
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    running = SimpleNamespace(team_id="team-1", base_url="http://x", token="t")
    monkeypatch.setattr(daemon, "running", lambda _project_id: running)
    monkeypatch.setattr("cuttlefish.fleet.daemon.cancel_run", lambda **_kwargs: None)
    asked: list[str] = []

    async def interrupt(root: str) -> int:
        asked.append(root)
        return 1

    monkeypatch.setattr("cuttlefish.fleet.daemon.codex_live.interrupt_root", interrupt)

    await daemon.stop(project.id)

    assert asked == [project.root]


async def test_a_stop_that_fails_to_deliver_is_not_journaled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon = _new_daemon(tmp_path)
    project = daemon.projects.register(name="alpha", root=str(tmp_path / "alpha"))
    store = EpisodicStore.open(tmp_path / "episodic.db")
    daemon._team_stores["team-1"] = store
    running = SimpleNamespace(team_id="team-1", base_url="http://x", token="t")
    monkeypatch.setattr(daemon, "running", lambda _project_id: running)

    def refuse(**_kwargs: Any) -> None:
        raise SteeringDeliveryError("no such run")

    monkeypatch.setattr("cuttlefish.fleet.daemon.cancel_run", refuse)
    with pytest.raises(FleetError):
        await daemon.stop(project.id)
    assert list(store.read("team-1")) == []
    store.close()
