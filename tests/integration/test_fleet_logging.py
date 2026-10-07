"""Integration: what the daemon says in its log (ADR-0029 decision 4).

A real FastAPI app over a real `FleetDaemon`, with a deliberately missing kopicode binary,
the same fast real failure `test_fleet_daemon.py` uses. The dashboard shows only a generic
line for a failed start, so the log has to carry the real reason.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import satay.control
from starlette.testclient import TestClient

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.projects.store import ProjectStore


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_BIN", "cuttlefish-test-missing-kopicode-binary")
    monkeypatch.setenv("CUTTLEFISH_LLM_PROVIDER", "replay")
    monkeypatch.delenv("CUTTLEFISH_SECRETS_KEY", raising=False)


def _client(tmp_path: Path, *, token: str = "test-token") -> TestClient:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    app = create_app(daemon, security=satay.control.SecurityPolicy(token="test-token"))
    return TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: token})


def test_a_failed_start_logs_the_real_reason_at_both_the_daemon_and_the_http_layer(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client = _client(tmp_path)
    root = tmp_path / "alpha"
    root.mkdir()
    project = client.post("/api/projects", json={"name": "alpha", "root": str(root)}).json()

    with caplog.at_level(logging.INFO):
        response = client.post(
            f"/api/projects/{project['id']}/start",
            json={"roles": [{"name": "builder", "text": "do it"}]},
        )

    assert response.status_code == 409
    assert "cuttlefish-test-missing-kopicode-binary" in response.json()["detail"]
    by_logger = {r.name: r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING}
    assert "failed to start" in by_logger["cuttlefish.fleet.daemon"]
    assert "-> 409" in by_logger["cuttlefish.fleet.server"]
    assert "cuttlefish-test-missing-kopicode-binary" in by_logger["cuttlefish.fleet.server"]


def test_a_rejected_token_is_logged_without_the_token(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client = _client(tmp_path, token="a-wrong-token-value")

    with caplog.at_level(logging.WARNING, logger="cuttlefish.fleet.server"):
        response = client.get("/api/projects")

    assert response.status_code == 401
    assert "rejected" in caplog.text and "GET /api/projects" in caplog.text
    assert "a-wrong-token-value" not in caplog.text
    assert "test-token" not in caplog.text


def test_an_unknown_project_is_routine_and_stays_below_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client = _client(tmp_path)

    with caplog.at_level(logging.WARNING, logger="cuttlefish.fleet.server"):
        response = client.post(
            "/api/projects/nope/start", json={"roles": [{"name": "a", "text": "b"}]}
        )

    assert response.status_code == 404
    assert caplog.records == []
