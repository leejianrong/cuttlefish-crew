"""Integration (CUT-119): a browser, the relay and the daemon's connector on loopback, with a
Python browser speaking the protocol. The real relay and connector run on real sockets. The daemon
app is the fleet's own for the routes a dashboard uses, and a small app where a test needs control
of the bytes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import satay.control
from fastapi import FastAPI, Request, WebSocket
from relay_rig import Browser, Server, Tap, free_port, relay_app

from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.relay_link import RelayAwareCheck, RelayLink, RelaySettings
from cuttlefish.fleet.server import create_app
from cuttlefish.projects.store import ProjectStore
from cuttlefish.relay.connector import RelayConnector, load_or_create_daemon_key, room_id_for
from cuttlefish.relay.protocol import public_bytes

TOKEN_HEADER = "x-cuttlefish-token"
SECRET_MARKER = b"MARKER-a1f3c9-do-not-leak"


def _echo_app(check: RelayAwareCheck) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        try:
            check.check(token=request.headers.get(TOKEN_HEADER), host=None, origin=None)
        except satay.control.AuthError as exc:
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": exc.detail}, status_code=exc.status)
        return await call_next(request)

    @app.get("/api/seen-token")
    async def seen_token(request: Request) -> dict[str, str | None]:
        return {"token": request.headers.get(TOKEN_HEADER), "host": request.headers.get("host")}

    @app.post("/api/echo")
    async def echo(request: Request) -> dict[str, int | str]:
        body = await request.body()
        return {"length": len(body), "head": body[:8].decode(errors="replace")}

    @app.get("/api/big")
    async def big(n: int) -> bytes:  # type: ignore[type-var]
        from fastapi import Response

        return Response(content=bytes(i % 251 for i in range(n)))  # type: ignore[return-value]

    @app.get("/api/slow")
    async def slow() -> dict[str, str]:
        await asyncio.sleep(0.6)
        return {"done": "slow"}

    @app.get("/api/fast")
    async def fast() -> dict[str, str]:
        return {"done": "fast"}

    @app.websocket("/api/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        first = json.loads(await socket.receive_text())
        try:
            check.check(token=first.get("token"), host=None, origin=None)
        except satay.control.AuthError:
            await socket.close(code=4401, reason="not authorised")
            return
        await socket.send_text(
            json.dumps({"type": "ready", "cols": first.get("size", {}).get("cols")})
        )
        while True:
            message = await socket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                await socket.send_bytes(message["bytes"][::-1])
            elif message.get("text") == "bye":
                await socket.close(code=4000, reason="bye")
                return

    return app


@dataclass
class Rig:
    relay: Server
    connector: RelayConnector
    tap: Tap
    public: bytes
    psk: bytes
    internal: str

    async def browser(self, psk: bytes | None = None) -> Browser:
        return await Browser.connect(
            self.relay.ws_url, self.connector.room, self.public, psk or self.psk
        )


async def _rig(tmp_path: Path, make_app, *, delays=(0.05,)) -> AsyncIterator[Rig]:  # type: ignore[no-untyped-def]
    tap = Tap()
    relay = Server(relay_app(tap))
    await relay.start()
    link = RelayLink.create(RelaySettings(relay.ws_url, tmp_path / "relay-key"))
    app = make_app(link)
    connector = link.connector(app)
    connector._delays = delays
    task = asyncio.create_task(connector.run())
    await asyncio.wait_for(connector.connected.wait(), 5)
    rig = Rig(
        relay,
        connector,
        tap,
        public_bytes(load_or_create_daemon_key(link.settings.key_path)),
        link.psk,
        link.internal_token,
    )
    try:
        yield rig
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await relay.stop()


@pytest.fixture
async def rig(tmp_path: Path) -> AsyncIterator[Rig]:
    def make(link: RelayLink) -> FastAPI:
        return _echo_app(
            RelayAwareCheck(satay.control.SecurityPolicy(token="loopback"), link.internal_token)
        )  # type: ignore[arg-type]

    async for r in _rig(tmp_path, make):
        yield r


@pytest.fixture
async def fleet_rig(tmp_path: Path) -> AsyncIterator[tuple[Rig, ProjectStore]]:
    store = ProjectStore.open(tmp_path / "projects.db")
    root = tmp_path / "alpha"
    root.mkdir()
    store.register(name="alpha", root=str(root), backend="claude-code")

    def make(link: RelayLink) -> FastAPI:
        return create_app(
            FleetDaemon(store), security=link.wrap(satay.control.SecurityPolicy(token="loopback"))
        )

    async for r in _rig(tmp_path / "k", make):
        yield r, store
    store.close()


async def test_the_dashboards_own_routes_work_through_the_relay(
    fleet_rig: tuple[Rig, ProjectStore],
) -> None:
    rig, _store = fleet_rig
    browser = await rig.browser()
    try:
        status, _headers, body = await browser.request("GET", "/api/auth-mode")
        assert status == 200 and json.loads(body) == {"mode": "token"}
        status, _headers, body = await browser.request("GET", "/api/projects")
        assert status == 200 and [p["name"] for p in json.loads(body)["projects"]] == ["alpha"]
        status, _headers, body = await browser.request("GET", "/api/projects/nope/agent-session")
        assert status == 404 and "detail" in json.loads(body)
    finally:
        await browser.close()


async def test_the_app_sees_the_connectors_token_and_never_one_the_browser_sent(rig: Rig) -> None:
    browser = await rig.browser()
    try:
        status, _h, body = await browser.request(
            "GET", "/api/seen-token", headers={TOKEN_HEADER: "attacker", "host": "evil.example"}
        )
        assert status == 200
        seen = json.loads(body)
        assert seen["token"] == rig.internal and seen["host"] == "127.0.0.1"
    finally:
        await browser.close()


async def test_a_browser_with_the_wrong_psk_gets_nothing(rig: Rig) -> None:
    from cuttlefish.relay.protocol import NoiseError

    # Its handshake reply decrypts under a different psk, so the browser's own side fails.
    with pytest.raises(NoiseError):
        await Browser.connect(rig.relay.ws_url, rig.connector.room, rig.public, bytes(32))
    # The daemon is still serving a browser that holds the right psk.
    good = await rig.browser()
    try:
        status, _h, _b = await good.request("GET", "/api/fast")
        assert status == 200
    finally:
        await good.close()


async def test_a_large_body_each_way_arrives_intact(rig: Rig) -> None:
    browser = await rig.browser()
    try:
        payload = b"ABCDEFGH" + bytes(range(256)) * 900  # about 230 KB, several frames
        status, _h, body = await browser.request("POST", "/api/echo", payload)
        assert status == 200 and json.loads(body) == {"length": len(payload), "head": "ABCDEFGH"}
        status, _h, big = await browser.request("GET", "/api/big?n=300000")
        assert status == 200 and big == bytes(i % 251 for i in range(300000))
    finally:
        await browser.close()


async def test_a_slow_request_does_not_hold_up_a_fast_one(rig: Rig) -> None:
    browser = await rig.browser()
    try:
        slow = asyncio.create_task(browser.request("GET", "/api/slow"))
        await asyncio.sleep(0.05)
        started = asyncio.get_running_loop().time()
        status, _h, body = await browser.request("GET", "/api/fast")
        elapsed = asyncio.get_running_loop().time() - started
        assert status == 200 and json.loads(body) == {"done": "fast"}
        assert elapsed < 0.4 and not slow.done()
        assert json.loads((await slow)[2]) == {"done": "slow"}
    finally:
        await browser.close()


async def test_a_websocket_authenticates_with_the_connectors_token_and_carries_bytes(
    rig: Rig,
) -> None:
    browser = await rig.browser()
    try:
        socket = await browser.websocket("/api/ws")
        await asyncio.wait_for(socket.accepted.wait(), 5)
        await socket.send_text(
            json.dumps(
                {"type": "auth", "token": "not-the-internal-one", "size": {"cols": 90, "rows": 30}}
            )
        )
        assert json.loads(str(await socket.recv())) == {"type": "ready", "cols": 90}
        await socket.send_bytes(b"abc\x00\xff")
        assert await socket.recv() == b"\xff\x00cba"
        await socket.send_text("bye")
        assert await socket.recv() == (4000, "bye")
    finally:
        await browser.close()


async def test_a_websocket_refused_by_the_app_closes_with_the_apps_code(rig: Rig) -> None:
    browser = await rig.browser()
    try:
        socket = await browser.websocket("/api/ws")
        await asyncio.wait_for(socket.accepted.wait(), 5)
        await socket.send_text("this is not an auth message")  # the app cannot parse it
        closed = await socket.recv()
        assert isinstance(closed, tuple) and closed[0] != 1000
    finally:
        await browser.close()


async def test_the_relay_never_sees_a_plaintext_marker(rig: Rig) -> None:
    import websockets

    # The control: the tap does record what a plaintext sender would put on the wire.
    raw = await websockets.connect(f"{rig.relay.ws_url}/ws/client/{rig.connector.room}")
    await raw.send(SECRET_MARKER)
    await raw.close()
    assert any(SECRET_MARKER in frame for frame in rig.tap.seen)
    rig.tap.seen.clear()

    browser = await rig.browser()
    try:
        status, _h, _b = await browser.request(
            "POST",
            f"/api/echo?q={SECRET_MARKER.decode()}",
            SECRET_MARKER * 3,
            headers={"x-note": SECRET_MARKER.decode()},
        )
        assert status == 200
        socket = await browser.websocket("/api/ws")
        await socket.send_text(json.dumps({"type": "auth", "token": "x"}))
        assert json.loads(str(await socket.recv()))["type"] == "ready"
        await socket.send_bytes(SECRET_MARKER)
        assert await socket.recv() == SECRET_MARKER[::-1]
    finally:
        await browser.close()
    assert len(rig.tap.seen) > 6
    for needle in (SECRET_MARKER, b"/api/echo", b"x-note", b"auth", b"ready"):
        assert not any(needle in frame for frame in rig.tap.seen), needle


async def test_a_browser_is_closed_when_the_daemon_is_offline(tmp_path: Path) -> None:
    relay = Server(relay_app())
    await relay.start()
    try:
        import websockets

        room = room_id_for(public_bytes(load_or_create_daemon_key(tmp_path / "k")))
        socket = await websockets.connect(f"{relay.ws_url}/ws/client/{room}")
        with pytest.raises(websockets.ConnectionClosed) as closed:
            await socket.recv()
        assert closed.value.rcvd is not None and closed.value.rcvd.code == 4503
    finally:
        await relay.stop()


async def test_the_connector_reconnects_when_the_relay_comes_back(tmp_path: Path) -> None:
    port = free_port()
    relay = Server(relay_app(), port)
    await relay.start()
    link = RelayLink.create(RelaySettings(relay.ws_url, tmp_path / "relay-key"))
    app = _echo_app(RelayAwareCheck(satay.control.SecurityPolicy(token="l"), link.internal_token))  # type: ignore[arg-type]
    connector = link.connector(app)
    connector._delays = (0.05,)
    task = asyncio.create_task(connector.run())
    try:
        await asyncio.wait_for(connector.connected.wait(), 5)
        await relay.stop()
        await asyncio.wait_for(_cleared(connector), 5)
        again = Server(relay_app(), port)
        await again.start()
        try:
            await asyncio.wait_for(connector.connected.wait(), 5)
            browser = await Browser.connect(
                again.ws_url, connector.room, public_bytes(connector.static), link.psk
            )
            status, _h, _b = await browser.request("GET", "/api/fast")
            assert status == 200
            await browser.close()
        finally:
            await again.stop()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _cleared(connector: RelayConnector) -> None:
    while connector.connected.is_set():
        await asyncio.sleep(0.01)
