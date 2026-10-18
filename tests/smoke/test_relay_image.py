"""Smoke (CUT-144): the relay image serves the page, a daemon registers a room, and a browser pairs
through it. Run against the image as built, never against source."""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.request
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import satay.control
import websockets
from relay_rig import Browser
from smoke_rig import Container, docker, wait_for

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.relay_link import RelayLink, RelaySettings
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.projects.store import ProjectStore
from cuttlefish.relay.connector import load_or_create_daemon_key
from cuttlefish.relay.protocol import generate_static, public_bytes
from cuttlefish.relay.protocol.envelope import CLOSE_UNAUTHORISED

OWNER_HEADERS = {TOKEN_HEADER: "loopback"}


async def make_room(relay: Container, label: str = "Smoke PC") -> tuple[str, str]:
    """Sign in as the owner and make a room, the way the dashboard will."""
    async with httpx.AsyncClient(base_url=relay.http) as http:
        signed_in = await http.post("/relay-api/session", json={"secret": relay.owner_secret})
        assert signed_in.status_code == 204, signed_in.text
        # The cookie is `Secure`, which a client does not send over plain http: send it by hand.
        cookie = signed_in.headers["set-cookie"].split(";")[0]
        made = await http.post(
            "/relay-api/rooms", json={"label": label}, headers={"cookie": cookie}
        )
        assert made.status_code == 201, made.text
        body = made.json()
        return body["id"], body["token"]


async def rooms(relay: Container) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(base_url=relay.http) as http:
        signed_in = await http.post("/relay-api/session", json={"secret": relay.owner_secret})
        cookie = signed_in.headers["set-cookie"].split(";")[0]
        listed = await http.get("/relay-api/rooms", headers={"cookie": cookie})
        return list(listed.json()["rooms"])


# -- the page --


def test_the_image_serves_the_dashboard_and_its_assets_with_a_strict_policy(
    relay: Container,
) -> None:
    with urllib.request.urlopen(f"{relay.http}/", timeout=5) as response:
        html = response.read().decode()
        headers = response.headers
    assert '<div id="app">' in html and "<title>cuttlefish-crew</title>" in html
    script = next(part.split('"')[0] for part in html.split('src="')[1:])
    with urllib.request.urlopen(f"{relay.http}{script}", timeout=5) as asset:
        assert asset.status == 200 and len(asset.read()) > 10_000
    csp = headers["content-security-policy"]
    assert "script-src 'self' 'sha256-" in csp and "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp
    assert headers["x-content-type-options"] == "nosniff"


def test_the_image_runs_as_a_user_that_is_not_root(relay: Container) -> None:
    assert docker("exec", relay.name, "id", "-u").stdout.strip() == "10001"


def test_the_image_refuses_to_run_an_open_relay_on_a_public_address(image: str) -> None:
    # The image defaults to hosted (CUTTLEFISH_RELAY_DB); blank that to ask for an open relay.
    result = docker(
        "run", "--rm", "-e", "CUTTLEFISH_RELAY_DB=", image,
        "cuttlefish", "relay", "--host", "0.0.0.0", check=False,
    )  # fmt: skip
    assert result.returncode == 2
    assert "only runs on loopback" in result.stderr


def test_the_image_refuses_to_start_hosted_with_no_owner_secret(image: str) -> None:
    result = docker("run", "--rm", "-e", "CUTTLEFISH_RELAY_OWNER_SECRET=", image, check=False)
    assert result.returncode == 2
    assert "at least 32 characters" in result.stderr


# -- a daemon registers a room --


async def test_a_daemon_with_the_token_registers_and_one_without_is_refused(
    relay: Container,
) -> None:
    room, token = await make_room(relay)
    async with websockets.connect(
        f"{relay.ws}/ws/daemon/{room}", additional_headers={"Authorization": "Bearer wrong"}
    ) as refused:
        with pytest.raises(websockets.ConnectionClosed) as closed:
            await asyncio.wait_for(refused.recv(), 5)
        assert closed.value.rcvd is not None and closed.value.rcvd.code == CLOSE_UNAUTHORISED
    async with websockets.connect(
        f"{relay.ws}/ws/daemon/{room}", additional_headers={"Authorization": f"Bearer {token}"}
    ):
        await asyncio.sleep(0.3)
        listed = {r["id"]: r for r in await rooms(relay)}
        assert listed[room]["online"] is True


async def test_a_room_and_its_token_survive_a_restart_of_the_container(relay: Container) -> None:
    room, token = await make_room(relay, "Survives")
    relay.restart()
    async with websockets.connect(
        f"{relay.ws}/ws/daemon/{room}", additional_headers={"Authorization": f"Bearer {token}"}
    ) as socket:
        await asyncio.sleep(0.3)
        assert socket.state.name == "OPEN"


# -- a browser pairs through it --


@pytest.fixture
async def paired_daemon(relay: Container, tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    """A real daemon (the fleet's own app and connector) registered in a room on the image."""
    room, token = await make_room(relay, "Pairing PC")
    link = RelayLink.create(
        RelaySettings(
            relay.ws, tmp_path / "relay-key", tmp_path / "devices.db", room=room, token=token
        )
    )
    store = ProjectStore.open(tmp_path / "projects.db")
    root = tmp_path / "alpha"
    root.mkdir()
    store.register(name="alpha", root=str(root), backend="claude-code")
    app = create_app(
        FleetDaemon(store),
        security=link.wrap(satay.control.SecurityPolicy(token="loopback")),
        relay=link,
    )
    connector = link.connector(app)
    task = asyncio.create_task(connector.run())
    await asyncio.wait_for(connector.connected.wait(), 10)
    owner = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")
    try:
        yield {
            "room": room,
            "link": link,
            "owner": owner,
            "public": public_bytes(load_or_create_daemon_key(tmp_path / "relay-key")),
        }
    finally:
        await owner.aclose()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        link.close()
        store.close()


async def test_a_browser_pairs_through_the_image_and_reads_the_dashboard(
    relay: Container, paired_daemon: dict[str, Any]
) -> None:
    link: RelayLink = paired_daemon["link"]
    owner: httpx.AsyncClient = paired_daemon["owner"]
    room: str = paired_daemon["room"]
    public: bytes = paired_daemon["public"]

    link.start_pairing()
    secret = link.pairing.current().secret  # type: ignore[union-attr]
    static = generate_static()
    browser = await Browser.pair(relay.ws, room, public, secret, "Smoke phone", static)
    waiting = await asyncio.wait_for(browser.controls.get(), 5)
    assert waiting["state"] == "waiting"
    pending = (await owner.get("/api/devices", headers=OWNER_HEADERS)).json()["pending"]
    assert [(p["name"], p["code"]) for p in pending] == [("Smoke phone", waiting["code"])]
    confirmed = await owner.post(
        f"/api/devices/pending/{pending[0]['id']}/confirm", headers=OWNER_HEADERS, json={}
    )
    assert confirmed.status_code == 200, confirmed.text
    paired = await asyncio.wait_for(browser.controls.get(), 5)
    assert paired["state"] == "paired"
    await browser.close()

    device_secret = base64.urlsafe_b64decode(paired["secret"] + "=" * (-len(paired["secret"]) % 4))
    again = await Browser.connect(relay.ws, room, public, device_secret, static)
    try:
        status, _headers, body = await again.request("GET", "/api/projects")
        assert status == 200
        assert [p["name"] for p in json.loads(body)["projects"]] == ["alpha"]
    finally:
        await again.close()
    wait_for(f"{relay.http}/healthz")
