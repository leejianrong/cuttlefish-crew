"""Integration (ADR-0043, CUT-135): the hosted relay, real sockets on loopback. Accounts own rooms,
a daemon needs its room's token, nothing else replaces a connected daemon, an address is limited,
and the relay serves the dashboard page with its security headers."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import websockets
from fastapi import FastAPI
from relay_rig import Browser, Server

from cuttlefish.fleet.relay_link import RelayLink, RelaySettings
from cuttlefish.fleet.tiers import Tier
from cuttlefish.relay.accounts import AccountStore
from cuttlefish.relay.connector import load_or_create_daemon_key
from cuttlefish.relay.protocol import generate_static, public_bytes
from cuttlefish.relay.protocol.envelope import CLOSE_UNAUTHORISED
from cuttlefish.relay.server import CLOSE_DAEMON_OFFLINE, CLOSE_FULL, create_relay_app

OWNER = "o" * 40

Hosted = Callable[..., Awaitable["Rig"]]


class Rig:
    def __init__(self, server: Server, store: AccountStore, http: httpx.AsyncClient) -> None:
        self.server = server
        self.store = store
        self.http = http

    async def sign_in(self) -> None:
        response = await self.http.post("/relay-api/session", json={"secret": OWNER})
        assert response.status_code == 204, response.text

    async def make_room(self, label: str = "Work PC") -> tuple[str, str]:
        response = await self.http.post("/relay-api/rooms", json={"label": label})
        assert response.status_code == 201, response.text
        body = response.json()
        return body["id"], body["token"]

    def daemon(self, room: str, token: str | None) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        return websockets.connect(
            f"{self.server.ws_url}/ws/daemon/{room}", additional_headers=headers
        )

    def client(self, room: str) -> Any:
        return websockets.connect(f"{self.server.ws_url}/ws/client/{room}")


async def _closed_with(socket: Any) -> int:
    with pytest.raises(websockets.ConnectionClosed) as info:
        await asyncio.wait_for(socket.recv(), 5)
    assert info.value.rcvd is not None
    return int(info.value.rcvd.code)


@pytest.fixture
async def hosted(tmp_path: Path) -> AsyncIterator[Hosted]:
    made: list[tuple[Server, httpx.AsyncClient, AccountStore]] = []

    async def make(**options: Any) -> Rig:
        store = AccountStore.open(":memory:")
        store.ensure_owner(OWNER)
        options.setdefault("secure_cookies", False)  # plain http on loopback
        server = Server(create_relay_app(store=store, **options))
        await server.start()
        http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{server.port}")
        made.append((server, http, store))
        return Rig(server, store, http)

    yield make
    for server, http, store in made:
        await http.aclose()
        await server.stop()
        store.close()


# -- sign-in and the rooms an account owns --


async def test_the_owner_signs_in_makes_a_room_and_sees_it(hosted: Hosted) -> None:
    rig = await hosted()
    assert (await rig.http.get("/relay-api/rooms")).status_code == 401
    bad = await rig.http.post("/relay-api/session", json={"secret": "x" * 40})
    assert bad.status_code == 401
    await rig.sign_in()
    room, token = await rig.make_room()
    assert len(room) >= 22 and len(token) >= 43
    listed = (await rig.http.get("/relay-api/rooms")).json()["rooms"]
    assert [(r["id"], r["label"], r["online"]) for r in listed] == [(room, "Work PC", False)]
    assert "token" not in listed[0]  # shown once, when it was made


async def test_the_session_cookie_is_host_prefixed_and_secure_when_hosted(hosted: Hosted) -> None:
    rig = await hosted(secure_cookies=True)
    response = await rig.http.post("/relay-api/session", json={"secret": OWNER})
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-cc_session=")
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie


async def test_a_request_from_another_site_is_refused(hosted: Hosted) -> None:
    rig = await hosted()
    await rig.sign_in()
    forged = await rig.http.post(
        "/relay-api/rooms", json={"label": "x"}, headers={"origin": "https://evil.example"}
    )
    assert forged.status_code == 403
    login = await rig.http.post(
        "/relay-api/session", json={"secret": OWNER}, headers={"origin": "https://evil.example"}
    )
    assert login.status_code == 403


async def test_sign_out_ends_the_session(hosted: Hosted) -> None:
    rig = await hosted()
    await rig.sign_in()
    assert (await rig.http.delete("/relay-api/session")).status_code == 204
    assert (await rig.http.get("/relay-api/rooms")).status_code == 401


async def test_too_many_sign_in_attempts_are_refused_even_with_the_right_secret(
    hosted: Hosted,
) -> None:
    rig = await hosted(sign_ins_per_minute=3)
    for _ in range(3):
        assert (
            await rig.http.post("/relay-api/session", json={"secret": "x" * 40})
        ).status_code == 401
    assert (await rig.http.post("/relay-api/session", json={"secret": OWNER})).status_code == 429


async def test_the_room_limit_is_said_in_words(hosted: Hosted) -> None:
    rig = await hosted()
    await rig.sign_in()
    for n in range(5):
        await rig.make_room(f"PC {n}")
    response = await rig.http.post("/relay-api/rooms", json={"label": "six"})
    assert response.status_code == 400 and "delete one" in response.json()["error"]


# -- the daemon's side --


async def test_a_daemon_needs_its_rooms_token(hosted: Hosted) -> None:
    rig = await hosted()
    await rig.sign_in()
    room, token = await rig.make_room()
    for wrong in (None, "wrong-token", token[::-1]):
        async with rig.daemon(room, wrong) as socket:
            assert await _closed_with(socket) == CLOSE_UNAUTHORISED
    async with rig.daemon("a-room-nobody-made", token) as socket:
        assert await _closed_with(socket) == CLOSE_UNAUTHORISED  # the same answer
    async with rig.daemon(room, token) as socket:
        await asyncio.sleep(0.1)
        listed = (await rig.http.get("/relay-api/rooms")).json()["rooms"]
        assert listed[0]["online"] is True
        assert socket.state.name == "OPEN"


async def test_a_second_daemon_with_a_wrong_token_does_not_replace_the_first(
    hosted: Hosted,
) -> None:
    rig = await hosted()
    await rig.sign_in()
    room, token = await rig.make_room()
    async with rig.daemon(room, token) as first:
        await asyncio.sleep(0.1)
        async with rig.daemon(room, "stolen-room-id-wrong-token") as intruder:
            assert await _closed_with(intruder) == CLOSE_UNAUTHORISED
        await asyncio.sleep(0.1)
        assert first.state.name == "OPEN"
        async with rig.client(room) as browser:  # still routed to the first daemon
            opened = await asyncio.wait_for(first.recv(), 5)
            assert "client-open" in str(opened)
            await browser.close()


async def test_a_daemon_with_the_right_token_replaces_its_own_stale_connection(
    hosted: Hosted,
) -> None:
    rig = await hosted()
    await rig.sign_in()
    room, token = await rig.make_room()
    async with rig.daemon(room, token) as stale:
        await asyncio.sleep(0.1)
        async with rig.daemon(room, token) as fresh:
            assert await _closed_with(stale) == 4000  # replaced by a newer connection
            await asyncio.sleep(0.1)
            assert fresh.state.name == "OPEN"


async def test_rotating_the_token_cuts_the_daemon_and_the_old_token_stops_working(
    hosted: Hosted,
) -> None:
    rig = await hosted()
    await rig.sign_in()
    room, old = await rig.make_room()
    async with rig.daemon(room, old) as socket:
        await asyncio.sleep(0.1)
        rotated = await rig.http.post(f"/relay-api/rooms/{room}/token")
        assert rotated.status_code == 200
        new = rotated.json()["token"]
        assert await _closed_with(socket) == CLOSE_UNAUTHORISED
    async with rig.daemon(room, old) as socket:
        assert await _closed_with(socket) == CLOSE_UNAUTHORISED
    async with rig.daemon(room, new) as socket:
        await asyncio.sleep(0.1)
        assert socket.state.name == "OPEN"


async def test_deleting_a_room_cuts_its_daemon(hosted: Hosted) -> None:
    rig = await hosted()
    await rig.sign_in()
    room, token = await rig.make_room()
    async with rig.daemon(room, token) as socket:
        await asyncio.sleep(0.1)
        assert (await rig.http.delete(f"/relay-api/rooms/{room}")).status_code == 204
        assert await _closed_with(socket) == CLOSE_UNAUTHORISED
    assert (await rig.http.get("/relay-api/rooms")).json()["rooms"] == []


async def test_a_browser_is_told_the_same_for_an_offline_a_missing_and_an_unmade_room(
    hosted: Hosted,
) -> None:
    rig = await hosted()
    await rig.sign_in()
    made, _token = await rig.make_room()
    for room in (made, "a-room-nobody-made-1", "short"):
        async with rig.client(room) as socket:
            assert await _closed_with(socket) == CLOSE_DAEMON_OFFLINE


async def test_bytes_and_connections_are_counted_per_room(hosted: Hosted) -> None:
    rig = await hosted()
    await rig.sign_in()
    room, token = await rig.make_room()
    async with rig.daemon(room, token) as daemon:
        await asyncio.sleep(0.1)
        async with rig.client(room) as browser:
            await asyncio.wait_for(daemon.recv(), 5)  # client-open
            await browser.send(b"x" * 1000)
            await asyncio.wait_for(daemon.recv(), 5)
        await asyncio.sleep(0.1)
    used, connections = rig.store.usage_today(room)
    assert used >= 1000 and connections == 1


# -- limits --


async def test_an_address_may_hold_only_so_many_connections(hosted: Hosted) -> None:
    rig = await hosted(max_connections_per_address=3)  # the daemon holds one
    await rig.sign_in()
    room, token = await rig.make_room()
    async with rig.daemon(room, token) as daemon:
        await asyncio.sleep(0.1)
        async with rig.client(room) as one, rig.client(room) as two:
            await asyncio.wait_for(daemon.recv(), 5)
            async with rig.client(room) as third:
                assert await _closed_with(third) == CLOSE_FULL
            assert one.state.name == "OPEN" and two.state.name == "OPEN"
    # The slots are given back, so the address can connect again.
    async with rig.daemon(room, token) as daemon:
        await asyncio.sleep(0.1)
        assert daemon.state.name == "OPEN"


async def test_new_connections_per_minute_are_limited(hosted: Hosted) -> None:
    rig = await hosted(new_connections_per_minute=3)
    codes = []
    for _ in range(4):
        async with rig.client("a-room-nobody-made-1") as socket:
            codes.append(await _closed_with(socket))
    assert codes == [CLOSE_DAEMON_OFFLINE] * 3 + [CLOSE_FULL]


async def test_guessing_tokens_locks_the_address_out_of_even_the_right_one(hosted: Hosted) -> None:
    rig = await hosted(auth_failures_per_minute=3)
    await rig.sign_in()
    room, token = await rig.make_room()
    for _ in range(3):
        async with rig.daemon(room, "guess") as socket:
            assert await _closed_with(socket) == CLOSE_UNAUTHORISED
    async with rig.daemon(room, token) as socket:
        assert await _closed_with(socket) == CLOSE_FULL


async def test_the_forwarded_address_counts_when_trusted_and_a_forged_one_does_not_help(
    hosted: Hosted,
) -> None:
    rig = await hosted(trust_forwarded=True, new_connections_per_minute=2)

    async def attempt(forwarded: str) -> int:
        async with websockets.connect(
            f"{rig.server.ws_url}/ws/client/a-room-nobody-made-1",
            additional_headers={"X-Forwarded-For": forwarded},
        ) as socket:
            return await _closed_with(socket)

    assert await attempt("1.1.1.1, 203.0.113.7") == CLOSE_DAEMON_OFFLINE
    assert await attempt("2.2.2.2, 203.0.113.7") == CLOSE_DAEMON_OFFLINE
    assert await attempt("3.3.3.3, 203.0.113.7") == CLOSE_FULL  # a new first entry changes nothing
    assert await attempt("3.3.3.3, 198.51.100.1") == CLOSE_DAEMON_OFFLINE  # another real address


async def test_a_forwarded_header_is_ignored_unless_the_proxy_is_trusted(hosted: Hosted) -> None:
    rig = await hosted(new_connections_per_minute=2)
    codes = []
    for n in range(3):
        async with websockets.connect(
            f"{rig.server.ws_url}/ws/client/a-room-nobody-made-1",
            additional_headers={"X-Forwarded-For": f"9.9.9.{n}"},
        ) as socket:
            codes.append(await _closed_with(socket))
    assert codes == [CLOSE_DAEMON_OFFLINE] * 2 + [CLOSE_FULL]


# -- the page --


async def test_the_relay_serves_the_dashboard_with_security_headers(
    hosted: Hosted, tmp_path: Path
) -> None:
    pages = tmp_path / "dist"
    pages.mkdir()
    inline = "document.documentElement.dataset.ready = '1'"
    (pages / "index.html").write_text(
        "<!doctype html><title>cuttlefish</title>"
        f"<script>{inline}</script><script type='module' src='/app.js'></script>"
    )
    (pages / "app.js").write_text("console.log(1)")
    rig = await hosted(pages_dir=pages)
    page = await rig.http.get("/")
    assert page.status_code == 200 and "cuttlefish" in page.text
    assert (await rig.http.get("/app.js")).status_code == 200
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert f"wss://127.0.0.1:{rig.server.port}" in csp
    digest = base64.b64encode(hashlib.sha256(inline.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in csp  # the page's own inline script, allowed by hash alone
    assert "unsafe-eval" not in csp and "script-src 'self' 'unsafe-inline'" not in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "no-referrer"
    # The page can never shadow the relay's own routes.
    assert (await rig.http.get("/relay-api/rooms")).status_code == 401
    assert (await rig.http.get("/healthz")).json()["ok"] is True
    assert (await rig.http.get("/relay-api/rooms")).headers["cache-control"] == "no-store"


async def test_a_hostile_host_header_never_reaches_the_policy(hosted: Hosted) -> None:
    rig = await hosted()
    response = await rig.http.get("/healthz", headers={"host": "evil.example; script-src *"})
    assert "evil.example" not in response.headers.get("content-security-policy", "")


# -- the real connector through it --


async def test_a_real_daemon_connector_and_a_browser_pair_through_a_hosted_relay(
    hosted: Hosted, tmp_path: Path
) -> None:
    from fastapi.responses import JSONResponse

    rig = await hosted()
    await rig.sign_in()
    room, token = await rig.make_room()
    link = RelayLink.create(
        RelaySettings(rig.server.ws_url, tmp_path / "relay-key", room=room, token=token)
    )
    assert link.room == room
    app = FastAPI()

    @app.get("/api/hello")
    async def hello() -> JSONResponse:
        return JSONResponse({"hello": "world"})

    connector = link.connector(app)
    connector._delays = (0.05,)
    task = asyncio.create_task(connector.run())
    try:
        await asyncio.wait_for(connector.connected.wait(), 5)
        assert connector.room == room
        static = generate_static()
        psk = secrets.token_bytes(32)
        link.store.create("phone", public_bytes(static), psk, Tier.ADMIN)
        browser = await Browser.connect(
            rig.server.ws_url,
            room,
            public_bytes(load_or_create_daemon_key(tmp_path / "relay-key")),
            psk,
            static,
        )
        try:
            status, _headers, body = await browser.request("GET", "/api/hello")
            assert status == 200 and json.loads(body) == {"hello": "world"}
        finally:
            await browser.close()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        link.close()


async def test_a_connector_with_a_wrong_token_never_connects_and_says_why(
    hosted: Hosted, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = await hosted()
    await rig.sign_in()
    room, _token = await rig.make_room()
    link = RelayLink.create(
        RelaySettings(rig.server.ws_url, tmp_path / "relay-key", room=room, token="wrong")
    )
    connector = link.connector(FastAPI())
    connector._delays = (0.05,)
    with caplog.at_level("WARNING"):
        task = asyncio.create_task(connector.run())
        await asyncio.sleep(0.5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    link.close()
    assert not connector.connected.is_set()
    assert "did not accept this room's registration token" in caplog.text
