"""Integration (CUT-120): pairing with a confirm on the PC, the device store, tiers enforced by the
daemon, and revocation. A real relay, a real connector and the fleet's own app; the owner acts
through the app's routes with the loopback token, a device through the relay as a Python browser."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import satay.control
import websockets
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from relay_rig import Browser, Server, relay_app

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.relay_link import RelayLink, RelaySettings
from cuttlefish.fleet.server import TOKEN_HEADER, create_app
from cuttlefish.fleet.tiers import Tier
from cuttlefish.projects.store import ProjectStore
from cuttlefish.relay.connector import load_or_create_daemon_key
from cuttlefish.relay.protocol import generate_static, public_bytes, short_code

OWNER = {TOKEN_HEADER: "loopback"}


@dataclass
class Rig:
    relay: Server
    link: RelayLink
    owner: httpx.AsyncClient
    public: bytes
    room: str
    project_id: str

    async def post(self, path: str, **body: Any) -> httpx.Response:
        return await self.owner.post(path, headers=OWNER, json=body)

    async def pair(self, name: str = "Test phone", tier: str | None = None) -> Paired:
        """A whole pairing, the way a person does it."""
        self.link.start_pairing()
        secret = self.link.pairing.current().secret  # type: ignore[union-attr]
        static = generate_static()
        browser = await Browser.pair(
            self.relay.ws_url, self.room, self.public, secret, name, static
        )
        waiting = await asyncio.wait_for(browser.controls.get(), 5)
        assert waiting["state"] == "waiting"
        pending = (await self.owner.get("/api/devices", headers=OWNER)).json()["pending"]
        assert [p["name"] for p in pending] == [name]
        confirmed = await self.post(
            f"/api/devices/pending/{pending[0]['id']}/confirm", **({"tier": tier} if tier else {})
        )
        assert confirmed.status_code == 200, confirmed.text
        paired = await asyncio.wait_for(browser.controls.get(), 5)
        assert paired["state"] == "paired"
        await browser.close()
        return Paired(paired["device"], bytes.fromhex(_unb64(paired["secret"])), static)

    async def connect(self, device: Paired) -> Browser:
        return await Browser.connect(
            self.relay.ws_url, self.room, self.public, device.secret, device.static
        )


@dataclass
class Paired:
    id: str
    secret: bytes
    static: X25519PrivateKey


def _unb64(text: str) -> str:
    import base64

    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).hex()


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    relay = Server(relay_app())
    await relay.start()
    link = RelayLink.create(RelaySettings(relay.ws_url, tmp_path / "relay-key"))
    store = ProjectStore.open(tmp_path / "projects.db")
    root = tmp_path / "alpha"
    root.mkdir()
    project = store.register(name="alpha", root=str(root), backend="claude-code")
    app = create_app(
        FleetDaemon(store),
        security=link.wrap(satay.control.SecurityPolicy(token="loopback")),
        relay=link,
    )
    connector = link.connector(app)
    connector._delays = (0.05,)
    task = asyncio.create_task(connector.run())
    await asyncio.wait_for(connector.connected.wait(), 5)
    owner = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1")
    try:
        yield Rig(
            relay,
            link,
            owner,
            public_bytes(load_or_create_daemon_key(link.settings.key_path)),
            connector.room,
            project.id,
        )
    finally:
        await owner.aclose()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await relay.stop()
        link.close()
        store.close()


# -- pairing -------------------------------------------------------------------------------------


async def test_a_phone_pairs_after_the_owner_confirms_and_both_screens_show_one_code(
    rig: Rig,
) -> None:
    rig.link.start_pairing()
    secret = rig.link.pairing.current().secret  # type: ignore[union-attr]
    browser = await Browser.pair(rig.relay.ws_url, rig.room, rig.public, secret, "Jian's phone")
    try:
        waiting = await asyncio.wait_for(browser.controls.get(), 5)
        assert waiting["state"] == "waiting"
        # The code on the phone is derived from its own end of the handshake; the PC shows its own.
        assert waiting["code"] == short_code(browser.handshake_hash)
        listed = (await rig.owner.get("/api/devices", headers=OWNER)).json()
        assert [(p["name"], p["code"]) for p in listed["pending"]] == [
            ("Jian's phone", waiting["code"])
        ]
        assert listed["devices"] == [] and listed["pairing"] is None  # the secret is spent
        confirmed = await rig.post(f"/api/devices/pending/{listed['pending'][0]['id']}/confirm")
        assert confirmed.json()["tier"] == "approve"
        paired = await asyncio.wait_for(browser.controls.get(), 5)
        assert paired["state"] == "paired" and paired["tier"] == "approve" and paired["secret"]
    finally:
        await browser.close()
    devices = (await rig.owner.get("/api/devices", headers=OWNER)).json()["devices"]
    assert [(d["name"], d["tier"]) for d in devices] == [("Jian's phone", "approve")]


async def test_a_paired_device_connects_again_with_its_own_secret_and_key(rig: Rig) -> None:
    device = await rig.pair()
    browser = await rig.connect(device)
    try:
        status, _h, body = await browser.request("GET", "/api/projects")
        assert status == 200 and [p["name"] for p in json.loads(body)["projects"]] == ["alpha"]
    finally:
        await browser.close()


async def test_nothing_is_served_to_a_device_that_is_waiting_for_the_owner(rig: Rig) -> None:
    rig.link.start_pairing()
    secret = rig.link.pairing.current().secret  # type: ignore[union-attr]
    browser = await Browser.pair(rig.relay.ws_url, rig.room, rig.public, secret)
    try:
        assert (await asyncio.wait_for(browser.controls.get(), 5))["state"] == "waiting"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(browser.request("GET", "/api/projects"), 2)
        await asyncio.wait_for(browser.closed.wait(), 5)  # asking for anything ends the session
    finally:
        await browser.close()
    assert (await rig.owner.get("/api/devices", headers=OWNER)).json()["devices"] == []


async def test_a_photographed_code_pairs_nobody_without_the_owner(rig: Rig) -> None:
    """A stranger who photographed the QR code connects first. The owner's own phone is then
    turned away, the stranger sits in the waiting room, and the owner turns them down."""
    rig.link.start_pairing()
    secret = rig.link.pairing.current().secret  # type: ignore[union-attr]
    stranger = await Browser.pair(rig.relay.ws_url, rig.room, rig.public, secret, "Not my phone")
    try:
        waiting = await asyncio.wait_for(stranger.controls.get(), 5)
        assert waiting["state"] == "waiting"
        # The secret is spent: the owner's phone, scanning the same code, is refused.
        with pytest.raises(websockets.ConnectionClosed):
            owners = await Browser.pair(rig.relay.ws_url, rig.room, rig.public, secret)
            await asyncio.wait_for(owners._socket.recv(), 5)
        pending = (await rig.owner.get("/api/devices", headers=OWNER)).json()["pending"]
        assert [p["name"] for p in pending] == ["Not my phone"]
        rejected = await rig.post(f"/api/devices/pending/{pending[0]['id']}/reject")
        assert rejected.status_code == 200
        assert (await asyncio.wait_for(stranger.controls.get(), 5))["state"] == "rejected"
    finally:
        await stranger.close()
    assert (await rig.owner.get("/api/devices", headers=OWNER)).json()["devices"] == []


async def test_a_pairing_secret_works_once(rig: Rig) -> None:
    await rig.pair("first")
    # The first device used and spent the secret; there is no open pairing left to use.
    assert rig.link.pairing.current() is None
    with pytest.raises(websockets.ConnectionClosed):
        again = await Browser.pair(rig.relay.ws_url, rig.room, rig.public, bytes(32))
        await asyncio.wait_for(again._socket.recv(), 5)


async def test_a_pairing_secret_expires(rig: Rig) -> None:
    rig.link.start_pairing()
    secret = rig.link.pairing.current().secret  # type: ignore[union-attr]
    clock = rig.link.pairing._now
    rig.link.pairing._now = lambda: clock() + 6 * 60  # six minutes later
    try:
        with pytest.raises(websockets.ConnectionClosed):
            late = await Browser.pair(rig.relay.ws_url, rig.room, rig.public, secret)
            await asyncio.wait_for(late._socket.recv(), 5)
    finally:
        rig.link.pairing._now = clock


async def test_a_device_the_owner_does_not_answer_is_turned_away_when_the_wait_ends(
    rig: Rig,
) -> None:
    rig.link.pairing._pending_ttl = 0.3
    rig.link.start_pairing()
    secret = rig.link.pairing.current().secret  # type: ignore[union-attr]
    browser = await Browser.pair(rig.relay.ws_url, rig.room, rig.public, secret)
    try:
        assert (await asyncio.wait_for(browser.controls.get(), 5))["state"] == "waiting"
        assert (await asyncio.wait_for(browser.controls.get(), 5))["state"] == "expired"
    finally:
        await browser.close()
    assert (await rig.owner.get("/api/devices", headers=OWNER)).json()["devices"] == []


async def test_a_new_device_cannot_be_confirmed_above_approve_and_gets_it_raised_later(
    rig: Rig,
) -> None:
    rig.link.start_pairing()
    secret = rig.link.pairing.current().secret  # type: ignore[union-attr]
    browser = await Browser.pair(rig.relay.ws_url, rig.room, rig.public, secret)
    try:
        await asyncio.wait_for(browser.controls.get(), 5)
        pending = (await rig.owner.get("/api/devices", headers=OWNER)).json()["pending"][0]["id"]
        refused = await rig.post(f"/api/devices/pending/{pending}/confirm", tier="admin")
        assert refused.status_code == 400
        assert (
            await rig.post(f"/api/devices/pending/{pending}/confirm", tier="view")
        ).status_code == 200
    finally:
        await browser.close()


# -- tiers ---------------------------------------------------------------------------------------


async def _status(browser: Browser, method: str, path: str, body: bytes = b"") -> tuple[int, str]:
    status, _h, payload = await browser.request(method, path, body)
    try:
        detail = json.loads(payload).get("detail", "")
    except ValueError:
        detail = ""
    return status, str(detail)


async def test_an_approve_device_reads_and_answers_but_cannot_change_settings(rig: Rig) -> None:
    device = await rig.pair()
    browser = await rig.connect(device)
    pid = rig.project_id
    try:
        assert (await _status(browser, "GET", "/api/projects"))[0] == 200
        assert (await _status(browser, "GET", f"/api/projects/{pid}/events"))[0] == 200
        # Approve-level routes get through the tier check (the app then says what it thinks).
        assert (await _status(browser, "POST", f"/api/projects/{pid}/stop"))[0] != 403
        steer = await _status(
            browser, "POST", f"/api/projects/{pid}/steer", b'{"role":"a","text":"x"}'
        )
        assert steer[0] != 403
        # Everything above Approve is refused by the daemon, with a sentence a person can act on.
        for method, path in [
            ("PATCH", f"/api/projects/{pid}/mode"),
            ("PATCH", f"/api/projects/{pid}/roles"),
            ("GET", f"/api/projects/{pid}/secrets"),
            ("PUT", "/api/secrets/KEY"),
            ("POST", "/api/projects"),
            ("DELETE", f"/api/projects/{pid}"),
            ("GET", "/api/fs"),
            ("POST", "/api/devices/pairing"),
            ("PATCH", f"/api/devices/{device.id}"),
        ]:
            status, detail = await _status(browser, method, path, b"{}")
            assert status == 403, (method, path, status)
            assert "paired as approve" in detail and "Devices on the PC" in detail
        # A route nobody listed needs admin, so it fails closed.
        assert (await _status(browser, "GET", "/api/never-added"))[0] == 403
    finally:
        await browser.close()


async def test_a_view_device_cannot_answer_or_start_anything(rig: Rig) -> None:
    device = await rig.pair(tier="view")
    browser = await rig.connect(device)
    pid = rig.project_id
    try:
        assert (await _status(browser, "GET", "/api/projects"))[0] == 200
        for path in (f"/api/projects/{pid}/stop", f"/api/projects/{pid}/agent-session/start"):
            assert (await _status(browser, "POST", path))[0] == 403
    finally:
        await browser.close()


async def test_a_header_the_browser_sends_cannot_name_a_more_powerful_device(rig: Rig) -> None:
    admin = await rig.pair("admin phone")
    await rig.owner.patch(f"/api/devices/{admin.id}", headers=OWNER, json={"tier": "admin"})
    low = await rig.pair("low phone")
    browser = await rig.connect(low)
    try:
        status, _h, _b = await browser.request(
            "PATCH",
            f"/api/projects/{rig.project_id}/mode",
            b"{}",
            headers={"x-cuttlefish-device": admin.id},
        )
        assert status == 403
    finally:
        await browser.close()


async def _terminal_close(browser: Browser, project_id: str) -> int | str:
    socket = await browser.websocket(f"/api/projects/{project_id}/terminal")
    await asyncio.wait_for(socket.accepted.wait(), 5)
    await socket.send_text(json.dumps({"type": "auth", "token": "whatever"}))
    closed = await socket.recv()
    assert isinstance(closed, tuple)
    return closed[0]


async def test_the_terminal_needs_the_terminal_tier(rig: Rig) -> None:
    device = await rig.pair()
    browser = await rig.connect(device)
    try:
        assert await _terminal_close(browser, rig.project_id) == 4403
        await rig.owner.patch(f"/api/devices/{device.id}", headers=OWNER, json={"tier": "terminal"})
        # The tier is read on every request, so the change applies at once to the live session.
        assert await _terminal_close(browser, rig.project_id) == 4404  # passed; no such session
    finally:
        await browser.close()


async def test_the_owner_can_raise_and_lower_a_tier_and_the_device_feels_it_at_once(
    rig: Rig,
) -> None:
    device = await rig.pair()
    browser = await rig.connect(device)
    pid = rig.project_id
    try:
        assert (await _status(browser, "PATCH", f"/api/projects/{pid}/mode", b"{}"))[0] == 403
        await rig.owner.patch(f"/api/devices/{device.id}", headers=OWNER, json={"tier": "admin"})
        assert (await _status(browser, "PATCH", f"/api/projects/{pid}/mode", b"{}"))[0] != 403
        await rig.owner.patch(f"/api/devices/{device.id}", headers=OWNER, json={"tier": "view"})
        assert (await _status(browser, "POST", f"/api/projects/{pid}/stop"))[0] == 403
        assert (
            await rig.owner.patch(f"/api/devices/{device.id}", headers=OWNER, json={"tier": "root"})
        ).status_code == 400
    finally:
        await browser.close()


async def test_the_owners_own_session_is_not_held_to_a_tier(rig: Rig) -> None:
    for method, path in [("GET", "/api/fs"), ("GET", f"/api/projects/{rig.project_id}/secrets")]:
        response = await rig.owner.request(method, path, headers=OWNER)
        assert response.status_code != 403


# -- revocation ----------------------------------------------------------------------------------


async def test_revoking_a_device_cuts_its_live_session_and_refuses_its_next_handshake(
    rig: Rig,
) -> None:
    device = await rig.pair()
    browser = await rig.connect(device)
    try:
        assert (await _status(browser, "GET", "/api/projects"))[0] == 200
        revoked = await rig.owner.delete(f"/api/devices/{device.id}", headers=OWNER)
        assert revoked.status_code == 200 and revoked.json()["revoked_at"] is not None
        assert (await asyncio.wait_for(browser.controls.get(), 5)) == {"t": "revoked"}
        await asyncio.wait_for(browser.closed.wait(), 5)
    finally:
        await browser.close()
    with pytest.raises(websockets.ConnectionClosed):
        again = await rig.connect(device)
        await asyncio.wait_for(again._socket.recv(), 5)
    listed = (await rig.owner.get("/api/devices", headers=OWNER)).json()["devices"]
    assert listed[0]["revoked_at"] is not None


async def test_a_paired_device_can_revoke_another_but_a_view_device_cannot(rig: Rig) -> None:
    lost = await rig.pair("lost phone")
    mine = await rig.pair("my phone")
    viewer = await rig.pair("viewer", tier="view")
    on_viewer = await rig.connect(viewer)
    on_mine = await rig.connect(mine)
    try:
        status, _d = await _status(on_viewer, "DELETE", f"/api/devices/{lost.id}")
        assert status == 403
        status, _d = await _status(on_mine, "DELETE", f"/api/devices/{lost.id}")
        assert status == 200
    finally:
        await on_viewer.close()
        await on_mine.close()
    with pytest.raises(websockets.ConnectionClosed):
        again = await rig.connect(lost)
        await asyncio.wait_for(again._socket.recv(), 5)


async def test_what_a_device_may_see_of_the_relay_says_who_it_is(rig: Rig) -> None:
    device = await rig.pair("Jian's phone")
    browser = await rig.connect(device)
    try:
        _s, _h, body = await browser.request("GET", "/api/relay")
        info = json.loads(body)
        assert info["enabled"] is True
        assert info["you"]["name"] == "Jian's phone" and info["you"]["tier"] == "approve"
    finally:
        await browser.close()
    owner_view = (await rig.owner.get("/api/relay", headers=OWNER)).json()
    assert owner_view["enabled"] is True and owner_view["you"] is None


async def test_a_device_list_never_contains_a_key_or_a_secret(rig: Rig) -> None:
    await rig.pair()
    text = (await rig.owner.get("/api/devices", headers=OWNER)).text
    assert "secret" not in text and "public_key" not in text
    assert set(json.loads(text)["devices"][0]) == {
        "id",
        "name",
        "tier",
        "created_at",
        "last_seen_at",
        "revoked_at",
    }


async def test_the_tier_names_are_the_ones_the_adr_lists() -> None:
    assert [t.label for t in Tier] == ["view", "approve", "terminal", "admin"]
