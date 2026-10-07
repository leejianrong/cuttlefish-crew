"""Unit: a failed or refused delegation reaches the operational log, redacted (ADR-0029)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from cuttlefish.episodic.events import DelegationCompleted, DelegationFailed, TaskFailed
from cuttlefish.episodic.redact import Redactor
from cuttlefish.episodic.store import EpisodicStore


def _store(tmp_path: Path, secret: str | None = None) -> EpisodicStore:
    redactor = Redactor(["MY_SECRET"], lookup=lambda _name: secret) if secret else Redactor()
    return EpisodicStore.open(tmp_path / "episodic.db", redactor=redactor)


def test_a_failed_delegation_is_logged_with_its_role_and_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _store(tmp_path)
    with caplog.at_level(logging.WARNING, logger="cuttlefish.episodic.store"):
        store.append("t1", DelegationFailed(reason="stop=max_turns exit_code=4", role="builder"))
        store.append("t1", TaskFailed(error="stop=max_turns exit_code=4", role="builder"))
    store.close()

    first, second = caplog.records
    assert "DelegationFailed" in first.getMessage() and "max_turns" in first.getMessage()
    assert first.role == "builder"  # type: ignore[attr-defined]
    assert "TaskFailed" in second.getMessage()


def test_the_logged_reason_is_the_redacted_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _store(tmp_path, secret="sk-super-secret-value")
    with caplog.at_level(logging.WARNING, logger="cuttlefish.episodic.store"):
        store.append("t1", DelegationFailed(reason="401 for key sk-super-secret-value"))
    store.close()

    assert "sk-super-secret-value" not in caplog.text
    assert "401" in caplog.text


def test_a_completed_delegation_is_not_logged_as_a_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _store(tmp_path)
    with caplog.at_level(logging.WARNING, logger="cuttlefish.episodic.store"):
        store.append("t1", DelegationCompleted(summary="done", edited_paths=[]))
    store.close()

    assert caplog.records == []


def test_lifecycle_events_are_projected_at_info_and_calls_at_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from cuttlefish.episodic.events import DelegationStarted, ToolCallRecorded

    store = _store(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="cuttlefish.episodic.store"):
        store.append("team-1", DelegationStarted(task_text="do it", root="/work/p", role="builder"))
        store.append(
            "team-1",
            ToolCallRecorded(tool="run_shell", detail="pytest", status="ok", role="builder"),
        )
    store.close()

    started, call = caplog.records
    assert (started.levelno, call.levelno) == (logging.INFO, logging.DEBUG)
    assert "backend=kopicode root=/work/p" in started.getMessage()
    assert (started.team, started.role) == ("team-1", "builder")  # type: ignore[attr-defined]
    assert "run_shell ok pytest" in call.getMessage()


def test_a_failed_delegation_log_line_carries_its_kind_and_record(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _store(tmp_path)
    with caplog.at_level(logging.WARNING, logger="cuttlefish.episodic.store"):
        store.append(
            "t1",
            DelegationFailed(
                reason="stop=max_turns exit_code=4",
                role="builder",
                failure_kind="max_turns",
                record="/work/p/.kopicode/sessions/abc",
            ),
        )
    store.close()

    message = caplog.records[0].getMessage()
    assert "kind=max_turns" in message
    assert "record=/work/p/.kopicode/sessions/abc" in message


def test_an_install_is_projected_with_its_command_and_its_failure_in_words(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from cuttlefish.episodic.events import EnvironmentPrepared, EnvironmentPrepareStarted

    store = _store(tmp_path)
    with caplog.at_level(logging.INFO, logger="cuttlefish.episodic.store"):
        store.append(
            "t1",
            EnvironmentPrepareStarted(
                ecosystem="python", commands=[["uv", "sync", "--frozen"]], reason=".venv is missing"
            ),
        )
        store.append(
            "t1",
            EnvironmentPrepared(
                ecosystem="python",
                ok=False,
                exit_code=2,
                duration_s=1.5,
                tail="no matching distribution",
                failure="exit",
            ),
        )
    store.close()

    started, failed = caplog.records
    assert started.getMessage().endswith("python: uv sync --frozen (.venv is missing)")
    assert failed.levelno == logging.WARNING
    assert "failed (exit code 2) after 1.5s: no matching distribution" in failed.getMessage()
