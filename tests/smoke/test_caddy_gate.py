"""Smoke (CUT-144): the repository's Caddyfile in front of the image. Until the outside review
(ADR-0037) only the owner may load the site, and the daemon's websocket is the one route that skips
the password, because its room token gates it."""

from __future__ import annotations

import asyncio
import base64
import urllib.error
import urllib.request

import pytest
import websockets
from smoke_rig import Container, Gate
from test_relay_image import make_room


def status(url: str, *, auth: tuple[str, str] | None = None) -> int:
    request = urllib.request.Request(url)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def test_every_route_asks_for_the_password_except_the_daemons_socket(caddy: Gate) -> None:
    for path in ("/", "/healthz", "/relay-api/rooms", "/assets/x.js", "/ws/client/abcdefgh12"):
        assert status(caddy.http + path) == 401, path
    assert status(caddy.http + "/", auth=(caddy.user, "wrong")) == 401
    assert status(caddy.http + "/", auth=(caddy.user, caddy.password)) == 200
    assert status(caddy.http + "/healthz", auth=(caddy.user, caddy.password)) == 200
    # Reaches the relay (a plain GET to a websocket route is not a 401 from the gate).
    assert status(caddy.http + "/ws/daemon/abcdefgh12") != 401


@pytest.mark.parametrize(
    "path",
    [
        "/ws/daemon/../relay-api/rooms",
        "/ws/daemon/%2e%2e/relay-api/rooms",
        "/ws/daemon/%2E%2E/healthz",
        "/WS/DAEMON/../healthz",
        "//ws/daemon/../relay-api/rooms",
        "/ws/daemon/x/..%2f..%2frelay-api/rooms",
    ],
)
def test_the_daemon_route_cannot_be_used_to_walk_around_the_password(
    caddy: Gate, path: str
) -> None:
    import http.client
    import urllib.parse

    parts = urllib.parse.urlsplit(caddy.http)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    connection.request("GET", path)  # sent as written: no client tidies the path
    code = connection.getresponse().status
    connection.close()
    assert code in (401, 404), (path, code)


async def test_a_daemon_registers_through_the_gate_and_a_browser_needs_the_password(
    relay: Container, caddy: Gate
) -> None:
    room, token = await make_room(relay, "Gated PC")
    async with websockets.connect(
        f"{caddy.ws}/ws/daemon/{room}", additional_headers={"Authorization": f"Bearer {token}"}
    ) as daemon:
        await asyncio.sleep(0.3)
        assert daemon.state.name == "OPEN"
        with pytest.raises(websockets.InvalidStatus) as refused:
            async with websockets.connect(f"{caddy.ws}/ws/client/{room}"):
                pass
        assert refused.value.response.status_code == 401
        basic = base64.b64encode(f"{caddy.user}:{caddy.password}".encode()).decode()
        async with websockets.connect(
            f"{caddy.ws}/ws/client/{room}", additional_headers={"Authorization": f"Basic {basic}"}
        ) as browser:
            opened = await asyncio.wait_for(daemon.recv(), 5)
            assert "client-open" in str(opened)
            assert browser.state.name == "OPEN"
