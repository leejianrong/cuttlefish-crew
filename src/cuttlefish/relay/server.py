"""The relay service (ADR-0037, ADR-0043): a headless FastAPI app that pairs a daemon with the
browsers in its room and copies bytes between them.

It cannot read what it carries. A browser and the daemon run a Noise handshake through it, and every
message after the first is encrypted end to end. What it can see is which rooms exist, when a daemon
and each browser connect and leave, and how many bytes move and how often (see
`cuttlefish.relay.protocol.envelope` for the one thing it adds: a client id in front of each message
to the daemon).

**Two modes.** With an `AccountStore` (hosted, ADR-0043) a room belongs to an account, a daemon must
present the room's registration token, and a valid token replaces only its own stale connection.
Without one (`open` mode, loopback and a tailnet) there are no accounts: a room is a name that a
daemon claims by connecting first, and a second daemon under the same name replaces the first. Open
mode is refused on a public address by the CLI. In both modes there are per-address limits, and the
relay can serve the dashboard build (`pages_dir`) from the same origin, which gives pairing a secure
origin.

This module must not import the fleet, secrets or episodic code
(`tests/unit/relay/test_boundary.py`).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cuttlefish.relay.accounts import Account, AccountError, AccountStore, Room
from cuttlefish.relay.limits import (
    DAEMON_AUTH_FAILURES_PER_MINUTE,
    MAX_CONNECTIONS_PER_ADDRESS,
    NEW_CONNECTIONS_PER_MINUTE,
    SIGN_INS_PER_MINUTE,
    ConnectionCounter,
    RateWindow,
    client_address,
)
from cuttlefish.relay.protocol.envelope import (
    CLIENT_CLOSE,
    CLIENT_OPEN,
    CLOSE_CLIENT,
    CLOSE_UNAUTHORISED,
    EnvelopeError,
    control,
    parse_control,
    unwrap,
    wrap,
)
from cuttlefish.relay.protocol.noise import MAX_MESSAGE_LEN

_LOG = logging.getLogger("cuttlefish.relay.server")

ROOM_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
DEFAULT_RELAY_PORT = 8450
MAX_ROOMS = 1000
MAX_CLIENTS_PER_ROOM = 16

#: Close codes the relay uses, in the private range.
CLOSE_DAEMON_OFFLINE = 4503
CLOSE_REPLACED = 4000
CLOSE_FULL = 4429
CLOSE_BAD_ROOM = 4400

SESSION_COOKIE = "cc_session"
#: Written to the usage table this often, so a busy room is not a database write per frame.
USAGE_FLUSH_SECONDS = 60.0
_HOST = re.compile(r"^[A-Za-z0-9.\-]+(:[0-9]{1,5})?$")


class _Peer:
    """A websocket with one writer at a time, so two tasks never interleave a message."""

    def __init__(self, socket: WebSocket) -> None:
        self.socket = socket
        self._lock = asyncio.Lock()

    async def send_bytes(self, data: bytes) -> None:
        async with self._lock:
            await self.socket.send_bytes(data)

    async def send_text(self, text: str) -> None:
        async with self._lock:
            await self.socket.send_text(text)

    async def close(self, code: int, reason: str = "") -> None:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await self.socket.close(code=code, reason=reason)


@dataclass(slots=True)
class _Room:
    daemon: _Peer | None = None
    clients: dict[int, _Peer] = field(default_factory=dict)
    next_client: int = 1
    #: Hosted mode only: the owning account, and what moved since the last write to the store.
    account_id: str | None = None
    relayed: int = 0
    connections: int = 0


class RelayState:
    def __init__(self) -> None:
        self.rooms: dict[str, _Room] = {}

    def stats(self) -> dict[str, int]:
        return {
            "rooms": len(self.rooms),
            "daemons": sum(1 for room in self.rooms.values() if room.daemon is not None),
            "clients": sum(len(room.clients) for room in self.rooms.values()),
        }

    def flush_usage(self, store: AccountStore) -> None:
        for room_id, room in self.rooms.items():
            if room.relayed or room.connections:
                store.add_usage(room_id, room.relayed, room.connections)
                room.relayed = 0
                room.connections = 0


class _Credentials(BaseModel):
    secret: str


class _NewRoom(BaseModel):
    label: str


def _bearer(socket: WebSocket) -> str | None:
    value = socket.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


_INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)


def inline_script_hashes(pages_dir: Path) -> tuple[str, ...]:
    """CSP source expressions for the inline scripts in the page's own `index.html` (the dashboard
    sets a forced theme before first paint). Allowing these by hash keeps `script-src` free of
    `'unsafe-inline'`, so a script someone injects into the page still does not run."""
    index = pages_dir / "index.html"
    if not index.exists():
        return ()
    hashes = []
    for body in _INLINE_SCRIPT.findall(index.read_text()):
        digest = hashlib.sha256(body.encode()).digest()
        hashes.append(f"'sha256-{base64.b64encode(digest).decode()}'")
    return tuple(hashes)


def _security_headers(host: str | None, script_hashes: tuple[str, ...] = ()) -> dict[str, str]:
    """What every page and answer carries. The page's own script is the one thing end-to-end
    encryption cannot protect (ADR-0037), so it may load only from this origin and talk only to
    it."""
    scripts = " ".join(("'self'", *script_hashes))
    connect = "'self'"
    if host and _HOST.match(host):
        connect += f" wss://{host}"
    return {
        "Content-Security-Policy": (
            f"default-src 'none'; script-src {scripts}; style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data: blob:; font-src 'self' data:; connect-src {connect}; "
            "worker-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'; object-src 'none'"
        ),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }


def create_relay_app(
    state: RelayState | None = None,
    *,
    store: AccountStore | None = None,
    pages_dir: Path | None = None,
    trust_forwarded: bool = False,
    secure_cookies: bool = True,
    max_connections_per_address: int = MAX_CONNECTIONS_PER_ADDRESS,
    new_connections_per_minute: int = NEW_CONNECTIONS_PER_MINUTE,
    sign_ins_per_minute: int = SIGN_INS_PER_MINUTE,
    auth_failures_per_minute: int = DAEMON_AUTH_FAILURES_PER_MINUTE,
) -> FastAPI:
    state = state or RelayState()
    open_connections = ConnectionCounter(max_connections_per_address)
    new_connections = RateWindow(new_connections_per_minute)
    sign_ins = RateWindow(sign_ins_per_minute)
    auth_failures = RateWindow(auth_failures_per_minute)
    cookie_name = f"__Host-{SESSION_COOKIE}" if secure_cookies else SESSION_COOKIE
    script_hashes = inline_script_hashes(pages_dir) if pages_dir is not None else ()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        flusher = asyncio.create_task(_flush_loop()) if store is not None else None
        try:
            yield
        finally:
            if flusher is not None:
                flusher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await flusher
            if store is not None:
                state.flush_usage(store)

    async def _flush_loop() -> None:
        assert store is not None
        while True:
            await asyncio.sleep(USAGE_FLUSH_SECONDS)
            state.flush_usage(store)
            sign_ins.forget_idle()
            new_connections.forget_idle()
            auth_failures.forget_idle()

    app = FastAPI(title="cuttlefish-crew relay", lifespan=lifespan)
    app.state.relay = state

    def address_of_request(request: Request) -> str:
        peer = request.client.host if request.client else None
        return client_address(peer, request.headers, trust_forwarded=trust_forwarded)

    def address_of_socket(socket: WebSocket) -> str:
        peer = socket.client.host if socket.client else None
        return client_address(peer, socket.headers, trust_forwarded=trust_forwarded)

    @app.middleware("http")
    async def headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in _security_headers(request.headers.get("host"), script_hashes).items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/relay-api") or request.url.path == "/healthz":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, **state.stats()}

    # -- the owner's own endpoints: sign in, and the rooms an account owns ----------------------

    def refuse(status: int, message: str) -> JSONResponse:
        return JSONResponse({"error": message}, status_code=status)

    def same_origin(request: Request) -> bool:
        origin = request.headers.get("origin")
        return origin is None or urlsplit(origin).netloc == request.headers.get("host")

    def signed_in(request: Request) -> Account | None:
        return store.account_for_session(request.cookies.get(cookie_name)) if store else None

    def room_json(room: Room) -> dict[str, object]:
        live = state.rooms.get(room.id)
        relayed, connections = store.usage_today(room.id) if store else (0, 0)
        if live is not None:
            relayed += live.relayed
            connections += live.connections
        return {
            "id": room.id,
            "label": room.label,
            "created": room.created,
            "status": room.status,
            "online": live is not None and live.daemon is not None,
            "last_online": room.last_online,
            "bytes_today": relayed,
            "connections_today": connections,
        }

    if store is not None:

        @app.post("/relay-api/session")
        async def sign_in(request: Request, body: _Credentials) -> Response:
            if not same_origin(request):
                return refuse(403, "that request came from another site")
            if not sign_ins.hit(address_of_request(request)):
                return refuse(429, "too many sign-in attempts; wait a minute")
            token = store.sign_in_owner(body.secret)
            if token is None:
                return refuse(401, "that is not the owner secret")
            response = Response(status_code=204)
            response.set_cookie(
                cookie_name,
                token,
                httponly=True,
                secure=secure_cookies,
                samesite="lax",
                max_age=30 * 24 * 3600,
                path="/",
            )
            return response

        @app.delete("/relay-api/session")
        async def sign_out(request: Request) -> Response:
            if not same_origin(request):
                return refuse(403, "that request came from another site")
            store.sign_out(request.cookies.get(cookie_name))
            response = Response(status_code=204)
            response.delete_cookie(cookie_name, path="/")
            return response

        @app.get("/relay-api/account")
        async def account(request: Request) -> Response:
            who = signed_in(request)
            if who is None:
                return refuse(401, "sign in first")
            return JSONResponse(
                {"id": who.id, "email": who.email, "display_name": who.display_name}
            )

        @app.get("/relay-api/rooms")
        async def list_rooms(request: Request) -> Response:
            who = signed_in(request)
            if who is None:
                return refuse(401, "sign in first")
            return JSONResponse({"rooms": [room_json(r) for r in store.rooms_of(who.id)]})

        @app.post("/relay-api/rooms")
        async def create_room(request: Request, body: _NewRoom) -> Response:
            who = signed_in(request)
            if who is None:
                return refuse(401, "sign in first")
            if not same_origin(request):
                return refuse(403, "that request came from another site")
            try:
                room, token = store.create_room(who.id, body.label)
            except AccountError as exc:
                return refuse(400, str(exc))
            # The token is shown here and never again: only its hash is kept.
            return JSONResponse({**room_json(room), "token": token}, status_code=201)

        @app.post("/relay-api/rooms/{room_id}/token")
        async def rotate(request: Request, room_id: str) -> Response:
            who = signed_in(request)
            if who is None:
                return refuse(401, "sign in first")
            if not same_origin(request):
                return refuse(403, "that request came from another site")
            try:
                token = store.rotate_token(who.id, room_id)
            except AccountError as exc:
                return refuse(404, str(exc))
            await _drop_daemon(room_id, "the registration token was replaced")
            return JSONResponse({"id": room_id, "token": token})

        @app.delete("/relay-api/rooms/{room_id}")
        async def delete_room(request: Request, room_id: str) -> Response:
            who = signed_in(request)
            if who is None:
                return refuse(401, "sign in first")
            if not same_origin(request):
                return refuse(403, "that request came from another site")
            if not store.delete_room(who.id, room_id):
                return refuse(404, "no such room")
            await _drop_daemon(room_id, "the room was deleted")
            return Response(status_code=204)

    async def _drop_daemon(room_id: str, reason: str) -> None:
        room = state.rooms.get(room_id)
        if room is not None and room.daemon is not None:
            await room.daemon.close(CLOSE_UNAUTHORISED, reason)

    # -- the daemon's side -----------------------------------------------------------------------

    @app.websocket("/ws/daemon/{room_id}")
    async def daemon_socket(socket: WebSocket, room_id: str) -> None:
        await socket.accept()
        peer = _Peer(socket)
        if not ROOM_PATTERN.match(room_id):
            await peer.close(CLOSE_BAD_ROOM, "bad room id")
            return
        address = address_of_socket(socket)
        if auth_failures.over(address) or not new_connections.hit(address):
            await peer.close(CLOSE_FULL, "too many connections from this address")
            return
        account_id: str | None = None
        if store is not None:
            granted = store.authorise_daemon(room_id, _bearer(socket))
            if granted is None:
                auth_failures.hit(address)
                await peer.close(CLOSE_UNAUTHORISED, "not authorised")
                return
            account_id = granted.account_id
            store.mark_online(room_id)
        if not open_connections.acquire(address):
            await peer.close(CLOSE_FULL, "too many connections from this address")
            return
        try:
            await _serve_daemon(socket, peer, room_id, account_id)
        finally:
            open_connections.release(address)

    async def _serve_daemon(
        socket: WebSocket, peer: _Peer, room_id: str, account_id: str | None
    ) -> None:
        if room_id not in state.rooms and len(state.rooms) >= MAX_ROOMS:
            await peer.close(CLOSE_FULL, "too many rooms")
            return
        room = state.rooms.setdefault(room_id, _Room())
        room.account_id = account_id
        previous = room.daemon
        room.daemon = peer
        if previous is not None:
            # A reconnecting daemon has lost its sessions with every browser: end them so the
            # browsers handshake again, and let the old connection go.
            await previous.close(CLOSE_REPLACED, "replaced by a newer connection")
            for client in list(room.clients.values()):
                await client.close(CLOSE_DAEMON_OFFLINE, "the daemon reconnected")
            room.clients.clear()
        _LOG.info("daemon connected to room %s", room_id[:8])
        try:
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    room.relayed += len(message["bytes"])
                    await _to_client(room, message["bytes"])
                elif message.get("text") is not None:
                    await _daemon_control(room, message["text"])
        except WebSocketDisconnect:
            pass
        finally:
            if room.daemon is peer:
                room.daemon = None
                for client in list(room.clients.values()):
                    await client.close(CLOSE_DAEMON_OFFLINE, "the daemon went offline")
                room.clients.clear()
                if store is not None:
                    store.add_usage(room_id, room.relayed, room.connections)
                    room.relayed = 0
                    room.connections = 0
                if state.rooms.get(room_id) is room:
                    del state.rooms[room_id]
            _LOG.info("daemon left room %s", room_id[:8])

    # -- a browser's side: no account, because the daemon's own pairing is the gate (ADR-0043) --

    @app.websocket("/ws/client/{room_id}")
    async def client_socket(socket: WebSocket, room_id: str) -> None:
        await socket.accept()
        peer = _Peer(socket)
        address = address_of_socket(socket)
        if not new_connections.hit(address) or not open_connections.acquire(address):
            await peer.close(CLOSE_FULL, "too many connections from this address")
            return
        try:
            await _serve_client(socket, peer, room_id)
        finally:
            open_connections.release(address)

    async def _serve_client(socket: WebSocket, peer: _Peer, room_id: str) -> None:
        room = state.rooms.get(room_id) if ROOM_PATTERN.match(room_id) else None
        # The same answer for a room that does not exist, one that is offline and one that is
        # suspended, so a room id cannot be probed.
        if room is None or room.daemon is None:
            await peer.close(CLOSE_DAEMON_OFFLINE, "the daemon is offline")
            return
        if len(room.clients) >= MAX_CLIENTS_PER_ROOM:
            await peer.close(CLOSE_FULL, "too many browsers in this room")
            return
        client_id = room.next_client
        room.next_client += 1
        room.clients[client_id] = peer
        room.connections += 1
        daemon = room.daemon
        try:
            await daemon.send_text(control(CLIENT_OPEN, client_id))
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                data = message.get("bytes")
                if data is None:
                    continue  # a browser only ever sends binary; ignore anything else
                if len(data) > MAX_MESSAGE_LEN:
                    await peer.close(1009, "message too large")
                    break
                room.relayed += len(data)
                await daemon.send_bytes(wrap(client_id, data))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            room.clients.pop(client_id, None)
            if room.daemon is daemon:
                with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                    await daemon.send_text(control(CLIENT_CLOSE, client_id))

    # The dashboard build, same origin, last so no page can shadow a route above it.
    if pages_dir is not None:
        app.mount("/", StaticFiles(directory=pages_dir, html=True), name="dashboard")

    return app


async def _to_client(room: _Room, data: bytes) -> None:
    try:
        client_id, payload = unwrap(data)
    except EnvelopeError:
        return
    client = room.clients.get(client_id)
    if client is not None:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await client.send_bytes(payload)


async def _daemon_control(room: _Room, text: str) -> None:
    try:
        kind, client_id, reason = parse_control(text)
    except EnvelopeError:
        return
    if kind == CLOSE_CLIENT:
        client = room.clients.pop(client_id, None)
        if client is not None:
            await client.close(1008, reason or "closed by the daemon")


async def run_relay(
    host: str,
    port: int,
    *,
    store: AccountStore | None = None,
    pages_dir: Path | None = None,
    trust_forwarded: bool = False,
    secure_cookies: bool = True,
) -> None:
    # A Noise message is at most MAX_MESSAGE_LEN, plus the client id the relay adds going to the
    # daemon: nothing larger is ever legitimate, so nothing larger is read.
    config = uvicorn.Config(
        create_relay_app(
            store=store,
            pages_dir=pages_dir,
            trust_forwarded=trust_forwarded,
            secure_cookies=secure_cookies,
        ),
        host=host,
        port=port,
        log_level="warning",
        ws_max_size=MAX_MESSAGE_LEN + 8,
        # The relay reads the proxy's address itself, and only when told to (`trust_forwarded`).
        proxy_headers=False,
    )
    await uvicorn.Server(config).serve()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m cuttlefish.relay.server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_RELAY_PORT)
    args = parser.parse_args()
    asyncio.run(run_relay(args.host, args.port))


if __name__ == "__main__":
    main()
