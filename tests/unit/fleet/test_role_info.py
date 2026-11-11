"""Unit (CUT-298): what each role row shows (its backend, model and age) without reading an idle
project's journal again on every poll."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cuttlefish.agents.registry import configured_model
from cuttlefish.episodic.events import DelegationStarted, TaskSubmitted
from cuttlefish.episodic.store import EpisodicEvent, EpisodicStore
from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.projects.store import ProjectStore, RoleDefinition

TEAM = "team-1"


def _project(tmp_path: Path) -> tuple[FleetDaemon, str, EpisodicStore]:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    root = tmp_path / "alpha"
    root.mkdir()
    project = daemon.projects.register(
        name="alpha",
        root=str(root),
        backend="kopicode",
        roles=[
            RoleDefinition(name="builder", persona="p", backend="codex"),
            RoleDefinition(name="reviewer", persona="p"),
        ],
    )
    daemon.projects.record_team_started(project.id, TEAM)
    return daemon, project.id, EpisodicStore.open(root / ".cuttlefish" / "episodic.db")


def _at(hour: int) -> datetime:
    return datetime(2026, 11, 12, hour, 0, tzinfo=UTC)


def test_each_role_has_its_own_last_event_time_and_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CUTTLEFISH_CODEX_MODEL", "gpt-test")
    monkeypatch.delenv("CUTTLEFISH_KOPICODE_PROVIDER_URL", raising=False)
    daemon, project_id, store = _project(tmp_path)
    store.append(TEAM, TaskSubmitted(text="t", role="builder"), ts=_at(9))
    store.append(TEAM, DelegationStarted(task_text="t", root="/r", role="builder"), ts=_at(10))
    store.append(TEAM, TaskSubmitted(text="t", role="reviewer"), ts=_at(11))
    project = daemon.projects.get(project_id)
    info = daemon.role_info(project, ["builder", "reviewer"], daemon.journal_view(project_id))
    assert info["builder"] == {
        "backend": "codex",
        "model": "gpt-test",
        "at": "2026-11-12T10:00:00+00:00",
    }
    # No backend of its own: the project's (so not repeated), and kopicode is asked for no model
    # unless told to.
    assert info["reviewer"] == {"at": "2026-11-12T11:00:00+00:00"}


def test_a_role_that_has_done_nothing_has_no_time(tmp_path: Path) -> None:
    daemon, project_id, _store = _project(tmp_path)
    project = daemon.projects.get(project_id)
    info = daemon.role_info(project, ["builder"], daemon.journal_view(project_id))
    assert "at" not in info["builder"]


def test_an_idle_journal_is_read_once_and_a_new_event_is_noticed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon, project_id, store = _project(tmp_path)
    store.append(TEAM, TaskSubmitted(text="t", role="builder"), ts=_at(9))
    reads: list[int] = []
    real = EpisodicStore.read

    def counting(self: EpisodicStore, task_id: str, *, after: int = 0) -> Iterator[EpisodicEvent]:
        reads.append(1)
        return real(self, task_id, after=after)

    monkeypatch.setattr(EpisodicStore, "read", counting)
    first = daemon.journal_view(project_id)
    for _ in range(5):
        assert daemon.journal_view(project_id) is first
    assert len(reads) == 1
    store.append(TEAM, DelegationStarted(task_text="t", root="/r", role="builder"), ts=_at(10))
    again = daemon.journal_view(project_id)
    assert len(reads) == 2 and again.status["builder"] == "working"
    # A new team, or a changed role list, is a different journal and is read afresh.
    daemon.projects.record_team_started(project_id, "team-2")
    assert daemon.journal_view(project_id).status["builder"] == "queued"


def test_a_model_is_named_only_where_cuttlefish_sets_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_PROVIDER_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_MODEL", "llama3")
    monkeypatch.delenv("CUTTLEFISH_CODEX_MODEL", raising=False)
    assert configured_model("kopicode") == "llama3"
    assert configured_model("codex") is None
    assert configured_model("claude-code") is None
    monkeypatch.delenv("CUTTLEFISH_KOPICODE_PROVIDER_URL")
    assert configured_model("kopicode") is None  # a model with no endpoint is not what runs
