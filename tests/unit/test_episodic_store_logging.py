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

    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "DelegationFailed" in m and "role=builder" in m and "max_turns" in m for m in messages
    )
    assert any("TaskFailed" in m for m in messages)


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
