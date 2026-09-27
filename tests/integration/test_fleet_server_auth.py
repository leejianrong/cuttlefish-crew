"""Integration: the fleet daemon's non-loopback password/session auth mode
(ADR-0011, KAN-1706) -- a real FastAPI app over a real `FleetDaemon`/
`ProjectStore`, exercised with Starlette's `TestClient`. Loopback/static-token
mode's own coverage stays in `test_fleet_server.py`, unchanged by this ADR.
"""

from __future__ import annotations

from pathlib import Path

import satay.control
from fastapi import FastAPI
from starlette.testclient import TestClient

from cuttlefish.fleet.auth import MIN_PASSWORD_LENGTH, SessionAuth
from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.projects.store import ProjectStore

_PASSWORD = "a" * MIN_PASSWORD_LENGTH


def _make_app(
    tmp_path: Path, *, allowed_origins: frozenset[str] = frozenset()
) -> tuple[FastAPI, SessionAuth]:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    login = SessionAuth(password=_PASSWORD, allowed_origins=allowed_origins)
    app = create_app(daemon, security=login, login=login, cors_origins=allowed_origins)
    return app, login


def test_auth_mode_reports_password_when_login_is_configured(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    response = TestClient(app, base_url="http://127.0.0.1").get("/api/auth-mode")
    assert response.json() == {"mode": "password"}


def test_auth_mode_reports_token_with_no_login_configured(tmp_path: Path) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    security = satay.control.SecurityPolicy(token="test-token")
    app = create_app(daemon, security=security)
    response = TestClient(app, base_url="http://127.0.0.1").get("/api/auth-mode")
    assert response.json() == {"mode": "token"}


def test_login_route_is_404_with_no_login_configured(tmp_path: Path) -> None:
    daemon = FleetDaemon(ProjectStore.open(tmp_path / "projects.db"))
    security = satay.control.SecurityPolicy(token="test-token")
    app = create_app(daemon, security=security)
    response = TestClient(app, base_url="http://127.0.0.1").post(
        "/api/login", json={"password": "whatever"}
    )
    assert response.status_code == 404


def test_login_with_the_right_password_yields_a_usable_session_token(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1")
    login_response = client.post("/api/login", json={"password": _PASSWORD})
    assert login_response.status_code == 200
    token = login_response.json()["token"]

    projects_response = client.get("/api/projects", headers={TOKEN_HEADER: token})
    assert projects_response.status_code == 200


def test_login_with_the_wrong_password_is_rejected(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    response = TestClient(app, base_url="http://127.0.0.1").post(
        "/api/login", json={"password": "wrong"}
    )
    assert response.status_code == 401


def test_request_with_no_session_token_is_rejected(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    response = TestClient(app, base_url="http://127.0.0.1").get("/api/projects")
    assert response.status_code == 401


def test_request_with_a_forged_session_token_is_rejected(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    response = TestClient(app, base_url="http://127.0.0.1").get(
        "/api/projects", headers={TOKEN_HEADER: "9999999999.deadbeef"}
    )
    assert response.status_code == 401


def test_request_from_a_disallowed_origin_is_rejected(tmp_path: Path) -> None:
    app, login = _make_app(tmp_path, allowed_origins=frozenset({"https://good.example"}))
    token = login.login(_PASSWORD)
    client = TestClient(app, base_url="http://127.0.0.1", headers={TOKEN_HEADER: token})

    ok = client.get("/api/projects", headers={"origin": "https://good.example"})
    assert ok.status_code == 200

    rejected = client.get("/api/projects", headers={"origin": "https://evil.example"})
    assert rejected.status_code == 403
