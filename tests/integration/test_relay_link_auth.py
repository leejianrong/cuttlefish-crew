"""Integration (CUT-119): the daemon's third security check next to the password session
(ADR-0011, ADR-0037). The connector's token is accepted, every other credential still goes to the
primary check, and wrapping it does not cost the sliding session its refresh header (ADR-0038)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import satay.control
from starlette.testclient import TestClient

from cuttlefish.fleet.auth import MIN_PASSWORD_LENGTH, SessionAuth
from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.relay_link import RelayAwareCheck
from cuttlefish.fleet.server import TOKEN_HEADER, TOKEN_REFRESH_HEADER, create_app
from cuttlefish.projects.store import ProjectStore

_PASSWORD = "p" * MIN_PASSWORD_LENGTH
_RELAY = "relay-token-for-tests"


def _client(tmp_path: Path) -> tuple[TestClient, SessionAuth]:
    login = SessionAuth(password=_PASSWORD)
    app = create_app(
        FleetDaemon(ProjectStore.open(tmp_path / "projects.db")),
        security=RelayAwareCheck(login, _RELAY),
        login=login,
    )
    return TestClient(app, base_url="http://127.0.0.1"), login


def test_the_connectors_token_opens_the_api_and_a_guess_does_not(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    assert client.get("/api/projects", headers={TOKEN_HEADER: _RELAY}).status_code == 200
    assert client.get("/api/projects", headers={TOKEN_HEADER: _RELAY + "x"}).status_code == 401
    assert client.get("/api/projects").status_code == 401


def test_a_password_session_still_works_beside_it(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    token = client.post("/api/login", json={"password": _PASSWORD}).json()["token"]
    assert client.get("/api/projects", headers={TOKEN_HEADER: token}).status_code == 200


def test_the_sliding_session_still_hands_out_a_fresh_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, login = _client(tmp_path)
    start = time.time()
    token = client.post("/api/login", json={"password": _PASSWORD}).json()["token"]
    monkeypatch.setattr(time, "time", lambda: start + login.ttl_seconds * 0.6)
    response = client.get("/api/projects", headers={TOKEN_HEADER: token})
    assert response.status_code == 200 and TOKEN_REFRESH_HEADER in response.headers


def test_the_loopback_token_check_is_deferred_to_too() -> None:
    check = RelayAwareCheck(satay.control.SecurityPolicy(token="loopback"), _RELAY)
    check.check(token="loopback", host="127.0.0.1", origin=None)
    check.check(token=_RELAY, host="evil.example", origin="https://evil.example")
    for bad in ("nope", "", None):
        with pytest.raises(satay.control.AuthError):
            check.check(token=bad, host="127.0.0.1", origin=None)


def test_a_terminal_token_that_is_not_ascii_is_refused_not_a_crash(tmp_path: Path) -> None:
    # A terminal's token arrives in a websocket message, where it can be any text. satay's own
    # check raises TypeError on that; the route turns it into the usual 4401.
    from starlette.websockets import WebSocketDisconnect

    app = create_app(
        FleetDaemon(ProjectStore.open(tmp_path / "projects.db")),
        security=satay.control.SecurityPolicy(token="loopback"),
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    with client.websocket_connect("/api/projects/p/terminal") as ws:
        ws.send_text('{"type": "auth", "token": "ünïcode"}')
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 4401
