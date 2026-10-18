"""Smoke (CUT-144, CUT-153): the compose stack, built from the image's own bundle. Caddy is in
front of the relay, and until the outside review (ADR-0037) only the owner may load the site.
The daemon's websocket is the one route that skips the password, because its room token gates it."""

from __future__ import annotations

import asyncio
import base64
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest
import websockets
from smoke_rig import Stack, docker
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


def test_every_route_asks_for_the_password_except_the_daemons_socket(stack: Stack) -> None:
    for path in ("/", "/healthz", "/relay-api/rooms", "/assets/x.js", "/ws/client/abcdefgh12"):
        assert status(stack.http + path) == 401, path
    assert status(stack.http + "/", auth=(stack.user, "wrong")) == 401
    assert status(stack.http + "/", auth=stack.basic) == 200
    assert status(stack.http + "/healthz", auth=stack.basic) == 200
    # Reaches the relay (a plain GET to a websocket route is not a 401 from the gate).
    assert status(stack.http + "/ws/daemon/abcdefgh12") != 401


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
    stack: Stack, path: str
) -> None:
    parts = urllib.parse.urlsplit(stack.http)
    assert parts.hostname and parts.port
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=5)
    connection.request("GET", path)  # sent as written: no client tidies the path
    code = connection.getresponse().status
    connection.close()
    assert code in (401, 404), (path, code)


async def test_a_daemon_registers_through_the_gate_and_a_browser_needs_the_password(
    stack: Stack,
) -> None:
    room, token = await make_room(stack, "Gated PC")
    async with websockets.connect(
        f"{stack.ws}/ws/daemon/{room}", additional_headers={"Authorization": f"Bearer {token}"}
    ) as daemon:
        await asyncio.sleep(0.3)
        assert daemon.state.name == "OPEN"
        with pytest.raises(websockets.InvalidStatus) as refused:
            async with websockets.connect(f"{stack.ws}/ws/client/{room}"):
                pass
        assert refused.value.response.status_code == 401
        basic = base64.b64encode(f"{stack.user}:{stack.password}".encode()).decode()
        async with websockets.connect(
            f"{stack.ws}/ws/client/{room}", additional_headers={"Authorization": f"Basic {basic}"}
        ) as browser:
            opened = await asyncio.wait_for(daemon.recv(), 5)
            assert "client-open" in str(opened)
            assert browser.state.name == "OPEN"


# -- the stack's own shape --


def test_the_relay_is_not_reachable_from_the_host_only_through_caddy(stack: Stack) -> None:
    assert docker("port", stack.container("relay")).stdout.strip() == ""
    networks = docker(
        "inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}",
        stack.container("relay"),
    ).stdout.split()  # fmt: skip
    assert networks == [f"{stack.project}_internal"]
    on_both = docker(
        "inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}",
        stack.container("caddy"),
    ).stdout.split()  # fmt: skip
    assert sorted(on_both) == sorted([f"{stack.project}_internal", f"{stack.project}_edge"])


def test_the_relay_has_no_route_to_the_internet(stack: Stack) -> None:
    probe = (
        "import urllib.request,sys\n"
        "try:\n"
        "    urllib.request.urlopen('http://1.1.1.1', timeout=3)\n"
        "except Exception:\n"
        "    sys.exit(0)\n"
        "sys.exit(1)\n"
    )
    result = docker("exec", stack.container("relay"), "python", "-c", probe, check=False)
    assert result.returncode == 0, "the relay reached the internet"


def test_both_containers_are_read_only_unprivileged_and_limited(stack: Stack) -> None:
    wanted_caps = {"relay": None, "caddy": ["CAP_NET_BIND_SERVICE"]}
    for service, caps in wanted_caps.items():
        host = json.loads(
            docker("inspect", "-f", "{{json .HostConfig}}", stack.container(service)).stdout
        )
        assert host["ReadonlyRootfs"] is True, service
        assert host["CapDrop"] == ["ALL"], service
        assert host["CapAdd"] == caps, service
        assert host["SecurityOpt"] == ["no-new-privileges:true"], service
        assert host["Privileged"] is False, service
        assert host["Memory"] > 0 and host["PidsLimit"] > 0, service


async def test_the_relay_counts_the_address_caddy_saw_not_one_a_client_made_up(
    stack: Stack,
) -> None:
    """Caddy replaces a client's X-Forwarded-For, so a forged one cannot dodge the address limit.
    This runs last: it spends the stack's budget of new connections for the minute."""
    basic = base64.b64encode(f"{stack.user}:{stack.password}".encode()).decode()
    codes = []
    for n in range(75):
        async with websockets.connect(
            f"{stack.ws}/ws/client/abcdefgh12",
            additional_headers={
                "Authorization": f"Basic {basic}",
                "X-Forwarded-For": f"9.9.9.{n}",
            },
        ) as socket:
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await asyncio.wait_for(socket.recv(), 5)
            assert closed.value.rcvd is not None
            codes.append(closed.value.rcvd.code)
    assert 4429 in codes, "every forged address was counted as a different client"
    assert codes.index(4429) <= 61


def test_the_bootstrap_helper_makes_a_hash_caddy_accepts_with_or_without_a_newline(
    stack: Stack,
) -> None:
    from smoke_rig import gate_hash

    for newline in (False, True):
        hashed = gate_hash("a-password", newline=newline)
        assert hashed.startswith("$2a$14$") and len(hashed) == 60
    assert gate_hash("a-password") != gate_hash("a-password")  # salted: two runs differ
    # The stack itself was started with a hash made by this helper, and the password works.
    assert status(stack.http + "/", auth=stack.basic) == 200
