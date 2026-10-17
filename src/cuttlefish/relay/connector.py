"""The daemon's side of the relay (ADR-0037, CUT-119): dials out, runs a Noise handshake with each
browser the relay introduces, and serves what arrives by calling the daemon's own ASGI
application in this process.

Nothing here imports the fleet. It is given an ASGI app, a key and a secret, and a token that
the app's own security check will accept (`internal_token`): once a browser has proved it holds
the keys (its first transport message decrypted, `session.PendingPeer.confirm`) the connector
speaks to the app as that browser, always with this token. It never forwards a token the browser
sent, and it swaps the token inside a terminal's first websocket message for the same reason.

Who may connect is the `Authority`'s say (ADR-0039): it names the secret for a device's key, or
for a key it has never seen, the open pairing's one-time secret. A key it has never seen can
only ever get as far as a waiting room, where no request is served until the owner has confirmed
it on the PC."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets
from collections.abc import Awaitable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import websockets
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from starlette.types import ASGIApp, Message, Scope

from cuttlefish.relay.protocol import (
    CHUNK_SIZE,
    WS_ACCEPTED,
    WS_BINARY,
    WS_TEXT,
    EstablishedPeer,
    Frame,
    FrameError,
    FrameType,
    NoiseError,
    PendingPeer,
    Role,
    StreamError,
    StreamMux,
    accept,
    decode,
    encode,
    generate_static,
    public_bytes,
    short_code,
    static_from_bytes,
)
from cuttlefish.relay.protocol.envelope import (
    CLIENT_CLOSE,
    CLIENT_OPEN,
    CLOSE_CLIENT,
    EnvelopeError,
    control,
    parse_control,
    unwrap,
    wrap,
)
from cuttlefish.relay.protocol.noise import MAX_MESSAGE_LEN

_LOG = logging.getLogger("cuttlefish.relay.connector")

#: The largest request body the connector will assemble. Bigger is reset, not buffered.
MAX_REQUEST_BODY = 32 * 1024 * 1024
#: Headers a browser may not set on the request the app sees (the connector sets its own).
_DROPPED_REQUEST_HEADERS = {
    "host",
    "origin",
    "referer",
    "connection",
    "content-length",
    "transfer-encoding",
    "x-cuttlefish-token",
    "x-cuttlefish-device",
}
_INTERNAL_HOST = b"127.0.0.1"


@dataclass(frozen=True, slots=True)
class PairingOutcome:
    """How a device's wait in the waiting room ended: `paired`, `rejected` or `expired`."""

    state: str
    device: str = ""
    secret: bytes = b""
    tier: str = ""


class PairingRequest(Protocol):
    async def decision(self) -> PairingOutcome: ...


class Authority(Protocol):
    """Who may connect. Implemented by the daemon (`fleet/devices.py`); the connector only asks."""

    def secret_for(self, public_key: bytes) -> bytes | None:
        """The psk to answer this key with: its device's own secret, the open pairing's secret for
        a key never seen, or `None` (a revoked device, or no pairing open)."""

    def device_for(self, public_key: bytes) -> str | None:
        """The id of the active device that owns this key, if any."""

    def begin_pairing(self, public_key: bytes, name: str, code: str) -> PairingRequest | None:
        """A key never seen has proved the pairing secret. `None` refuses it."""


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def room_id_for(daemon_public: bytes) -> str:
    """A room name that tells the relay nothing about the key it came from."""
    return hashlib.sha256(b"cuttlefish-relay-room" + daemon_public).hexdigest()[:32]


def load_or_create_daemon_key(path: Path) -> X25519PrivateKey:
    """The daemon's long-term key, created with mode 0600 the first time (never printed)."""
    if path.exists():
        return static_from_bytes(path.read_bytes())
    key = generate_static()
    raw = key.private_bytes_raw()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    return key


def pairing_payload(relay_url: str, daemon_key: X25519PrivateKey, pairing_secret: bytes) -> str:
    """What a browser needs to pair with this daemon through the relay, as one base64url string for
    a URL fragment (a fragment never reaches a server's logs). The secret in it works once."""
    public = public_bytes(daemon_key)
    body = {
        "v": 2,
        "relay": relay_url,
        "room": room_id_for(public),
        "daemon": b64u(public),
        "pairing": b64u(pairing_secret),
    }
    return b64u(json.dumps(body, separators=(",", ":")).encode())


# -- one browser -------------------------------------------------------------------------------


class _Request:
    def __init__(self, head: dict[str, Any]) -> None:
        self.head = head
        self.body = bytearray()


class _Socket:
    def __init__(self, head: dict[str, Any], internal_token: str, device: str) -> None:
        self.head = head
        self.internal_token = internal_token
        self.device = device
        # `connect` goes first, before the task that runs the app has even started, so a message
        # that arrives straight after the open cannot overtake it.
        self.inbox: asyncio.Queue[Message] = asyncio.Queue()
        self.inbox.put_nowait({"type": "websocket.connect"})
        self.first = True
        self.accepted = False
        self.closed = False


class _Session:
    """One browser's encrypted session with the daemon, over one relay client id."""

    def __init__(self, connector: RelayConnector, client: int) -> None:
        self._connector = connector
        self._client = client
        self._pending: PendingPeer | None = None
        self._peer: EstablishedPeer | None = None
        self._mux = StreamMux(Role.RESPONDER)
        self._send_lock = asyncio.Lock()
        self._requests: dict[int, _Request] = {}
        self._sockets: dict[int, _Socket] = {}
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._dead = False
        #: The id of the paired device this session belongs to; empty until it is one.
        self.device = ""
        self._waiting = False

    # -- what arrives ------------------------------------------------------------------------

    async def on_message(self, data: bytes) -> None:
        if self._dead:
            return
        try:
            if self._peer is not None:
                plain = self._peer.transport.decrypt(data)
                if self._waiting:
                    # In the waiting room nothing is served; a ping is all that is allowed.
                    if decode(plain).type is not FrameType.PING:
                        await self.drop("not paired yet")
                    return
                await self._on_plain(plain)
            elif self._pending is not None:
                # Noise gives no proof the caller holds the psk until a first message decrypts.
                self._peer = self._pending.confirm(data)
                self._pending = None
                await self._first_message(self._peer)
            else:
                self._pending = accept(
                    self._connector.static, self._connector.authority.secret_for, data
                )
                await self._connector.send_raw(wrap(self._client, self._pending.reply))
        except (NoiseError, FrameError, StreamError, EnvelopeError) as exc:
            _LOG.warning("relay client %s dropped: %s", self._client, exc)
            await self.drop("protocol error")

    async def _first_message(self, peer: EstablishedPeer) -> None:
        """The caller's first message decrypted, so it holds the keys. A device the authority
        knows carries on as itself. A key never seen goes to the waiting room."""
        authority = self._connector.authority
        device = authority.device_for(peer.peer_static)
        if device is not None:
            self.device = device
            await self._on_plain(peer.first_message)
            return
        hello = decode(peer.first_message)
        if hello.type is not FrameType.PING:
            raise FrameError("a new device must introduce itself with a ping")
        try:
            name = json.loads(hello.payload).get("name", "")
        except (ValueError, AttributeError):
            name = ""
        code = short_code(peer.transport.handshake_hash)
        request = authority.begin_pairing(peer.peer_static, str(name), code)
        if request is None:
            await self._send_control({"t": "pairing", "state": "refused"})
            self._drop_later("pairing refused")
            return
        self._waiting = True
        await self._send_control({"t": "pairing", "state": "waiting", "code": code})
        self._spawn(0, self._await_owner(request))

    async def _await_owner(self, request: PairingRequest) -> None:
        outcome = await request.decision()
        message: dict[str, object] = {"t": "pairing", "state": outcome.state}
        if outcome.state == "paired":
            message.update(device=outcome.device, secret=b64u(outcome.secret), tier=outcome.tier)
        await self._send_control(message)
        self._drop_later(f"pairing {outcome.state}")

    def _drop_later(self, reason: str) -> None:
        """Drop once this task is done, so a task never cancels itself."""
        asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(self.drop(reason)))

    async def _send_control(self, message: dict[str, object]) -> None:
        await self._send(Frame(FrameType.CONTROL, 0, json.dumps(message).encode()))

    async def revoke(self) -> None:
        """This device was revoked on the PC: say so, then end the session."""
        with contextlib.suppress(StreamError, NoiseError):
            await self._send_control({"t": "revoked"})
        await self.drop("revoked")

    async def drop(self, reason: str) -> None:
        """End this session and ask the relay to disconnect the browser."""
        await self.close()
        await self._connector.send_text(control(CLOSE_CLIENT, self._client, reason))

    async def close(self) -> None:
        self._dead = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _on_plain(self, plain: bytes) -> None:
        frame = self._mux.receive(decode(plain))
        stream = frame.stream
        kind = frame.type
        if kind is FrameType.PING:
            await self._send(self._mux.pong_for(frame))
        elif kind is FrameType.REQUEST:
            self._requests[stream] = _Request(_json_head(frame.payload))
        elif kind is FrameType.DATA and stream in self._requests:
            request = self._requests[stream]
            request.body.extend(frame.payload)
            if len(request.body) > MAX_REQUEST_BODY:
                self._requests.pop(stream, None)
                await self._send(self._mux.reset(stream, b"request body too large"))
        elif kind is FrameType.END and stream in self._requests:
            request = self._requests.pop(stream)
            self._spawn(stream, self._serve_http(stream, request))
        elif kind is FrameType.WS_OPEN:
            sock = _Socket(_json_head(frame.payload), self._connector.internal_token, self.device)
            self._sockets[stream] = sock
            self._spawn(stream, self._serve_websocket(stream, sock))
        elif kind is FrameType.WS_DATA and stream in self._sockets:
            self._sockets[stream].inbox.put_nowait(
                _ws_message(frame.payload, self._sockets[stream])
            )
        elif kind is FrameType.WS_CLOSE and stream in self._sockets:
            code = int.from_bytes(frame.payload[:2], "big") if len(frame.payload) >= 2 else 1005
            self._sockets[stream].inbox.put_nowait({"type": "websocket.disconnect", "code": code})
        elif kind is FrameType.RESET:
            self._requests.pop(stream, None)
            self._sockets.pop(stream, None)
            task = self._tasks.pop(stream, None)
            if task is not None:
                task.cancel()

    def _spawn(self, stream: int, work: Awaitable[None]) -> None:
        async def run() -> None:
            try:
                await work
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOG.exception("relay stream %s failed", stream)
            finally:
                self._tasks.pop(stream, None)

        self._tasks[stream] = asyncio.create_task(run())

    # -- what goes out -----------------------------------------------------------------------

    async def _send(self, *frames: Frame) -> None:
        peer = self._peer
        if peer is None or self._dead:
            return
        async with self._send_lock:
            for frame in frames:
                await self._connector.send_raw(
                    wrap(self._client, peer.transport.encrypt(encode(frame)))
                )

    # -- an HTTP request ---------------------------------------------------------------------

    async def _serve_http(self, stream: int, request: _Request) -> None:
        scope = _http_scope(
            request.head, len(request.body), self._connector.internal_token, self.device
        )
        body_sent = False
        finished = asyncio.Event()
        started = False

        async def receive() -> Message:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": bytes(request.body), "more_body": False}
            await finished.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                head = {
                    "status": message["status"],
                    "headers": [
                        [k.decode("latin-1"), v.decode("latin-1")]
                        for k, v in message.get("headers", [])
                    ],
                }
                await self._send(self._mux.respond(stream, json.dumps(head).encode()))
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    await self._send(*self._mux.send(stream, body))
                if not message.get("more_body", False):
                    await self._send(self._mux.close(stream))
                    finished.set()

        try:
            await self._connector.app(scope, receive, send)
        except asyncio.CancelledError:
            raise
        except StreamError:
            return  # the browser reset the stream
        except Exception:
            _LOG.exception("daemon app failed serving %s", request.head.get("path"))
            if not started:
                head = {"status": 500, "headers": [["content-type", "application/json"]]}
                with contextlib.suppress(StreamError):
                    await self._send(
                        self._mux.respond(stream, json.dumps(head).encode()),
                        *self._mux.send(stream, b'{"detail":"internal error"}'),
                        self._mux.close(stream),
                    )
            else:
                with contextlib.suppress(StreamError):
                    await self._send(self._mux.reset(stream, b"app failed"))
        finally:
            finished.set()

    # -- a websocket -------------------------------------------------------------------------

    async def _serve_websocket(self, stream: int, sock: _Socket) -> None:
        scope = _ws_scope(sock.head)

        async def receive() -> Message:
            return await sock.inbox.get()

        async def send(message: Message) -> None:
            kind = message["type"]
            if kind == "websocket.accept":
                sock.accepted = True
                await self._send(*self._ws_frames(stream, WS_ACCEPTED, b""))
            elif kind == "websocket.send":
                if message.get("bytes") is not None:
                    data: bytes = message["bytes"]
                    for start in range(0, max(len(data), 1), CHUNK_SIZE - 1):
                        await self._send(
                            *self._ws_frames(
                                stream, WS_BINARY, data[start : start + CHUNK_SIZE - 1]
                            )
                        )
                else:
                    await self._send(*self._ws_frames(stream, WS_TEXT, message["text"].encode()))
            elif kind == "websocket.close":
                await self._close_socket(
                    stream, sock, message.get("code", 1000), message.get("reason") or ""
                )

        try:
            await self._connector.app(scope, receive, send)
        except asyncio.CancelledError:
            raise
        except StreamError:
            return
        except Exception:
            _LOG.exception("daemon app failed on websocket %s", sock.head.get("path"))
            await self._close_socket(stream, sock, 1011, "internal error")
        else:
            await self._close_socket(stream, sock, 1000, "")

    def _ws_frames(self, stream: int, kind: int, data: bytes) -> list[Frame]:
        return self._mux.send(stream, bytes([kind]) + data)

    async def _close_socket(self, stream: int, sock: _Socket, code: int, reason: str) -> None:
        if sock.closed:
            return
        sock.closed = True
        with contextlib.suppress(StreamError):
            await self._send(
                self._mux.close(stream, code.to_bytes(2, "big") + reason.encode()[:120])
            )


def _json_head(payload: bytes) -> dict[str, Any]:
    try:
        head = json.loads(payload)
    except ValueError as exc:
        raise FrameError("stream head is not JSON") from exc
    if not isinstance(head, dict) or not isinstance(head.get("path"), str):
        raise FrameError("stream head has no path")
    return head


def _ws_message(payload: bytes, sock: _Socket) -> Message:
    if not payload:
        raise FrameError("empty websocket frame")
    kind, data = payload[0], payload[1:]
    if kind == WS_TEXT:
        text = data.decode()
        if sock.first:
            sock.first = False
            text = _with_internal_token(text, sock)
        return {"type": "websocket.receive", "text": text}
    if kind == WS_BINARY:
        sock.first = False
        return {"type": "websocket.receive", "bytes": data}
    raise FrameError("unknown websocket frame kind")


def _with_internal_token(text: str, sock: _Socket) -> str:
    """A terminal's first message is `{"type": "auth", "token": ...}`. The token in it is the
    browser's, which means nothing here: the app is given the connector's own."""
    try:
        message = json.loads(text)
    except ValueError:
        return text
    if isinstance(message, dict) and message.get("type") == "auth":
        message["token"] = sock.internal_token
        message["device"] = sock.device
        return json.dumps(message)
    return text


def _split_path(path: str) -> tuple[str, bytes]:
    base, _, query = path.partition("?")
    return base, query.encode()


def _http_scope(head: dict[str, Any], body_len: int, internal_token: str, device: str) -> Scope:
    path, query = _split_path(head["path"])
    headers: list[tuple[bytes, bytes]] = []
    for pair in head.get("headers", []):
        if (
            isinstance(pair, list)
            and len(pair) == 2
            and all(isinstance(v, str) for v in pair)
            and pair[0].lower() not in _DROPPED_REQUEST_HEADERS
        ):
            headers.append((pair[0].lower().encode("latin-1"), pair[1].encode("latin-1")))
    headers += [
        (b"host", _INTERNAL_HOST),
        (b"content-length", str(body_len).encode()),
        (b"x-cuttlefish-token", internal_token.encode()),
        (b"x-cuttlefish-device", device.encode()),
    ]
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": str(head.get("method", "GET")).upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 0),
    }


def _ws_scope(head: dict[str, Any]) -> Scope:
    path, query = _split_path(head["path"])
    return {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "root_path": "",
        "headers": [(b"host", _INTERNAL_HOST)],
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 0),
        "subprotocols": [],
    }


# -- the connection to the relay ---------------------------------------------------------------


class RelayConnector:
    def __init__(
        self,
        app: ASGIApp,
        *,
        relay_url: str,
        static: X25519PrivateKey,
        authority: Authority,
        internal_token: str | None = None,
        reconnect_delays: tuple[float, ...] = (0.5, 1.0, 2.0, 5.0, 10.0),
    ) -> None:
        self.app = app
        self.static = static
        self.authority = authority
        self.internal_token = internal_token or secrets.token_urlsafe(32)
        self._relay_url = relay_url.rstrip("/")
        self.room = room_id_for(public_bytes(static))
        self._delays = reconnect_delays
        self._socket: Any = None
        self._sessions: dict[int, _Session] = {}
        self.connected = asyncio.Event()

    async def revoke_device(self, device_id: str) -> None:
        """Cut every live session of a device that was just revoked."""
        for session in [s for s in self._sessions.values() if s.device == device_id]:
            await session.revoke()

    async def send_raw(self, data: bytes) -> None:
        socket = self._socket
        if socket is not None:
            with contextlib.suppress(websockets.ConnectionClosed):
                await socket.send(data)

    async def send_text(self, text: str) -> None:
        socket = self._socket
        if socket is not None:
            with contextlib.suppress(websockets.ConnectionClosed):
                await socket.send(text)

    async def run(self) -> None:
        """Stay connected to the relay until cancelled, retrying with a growing pause."""
        attempt = 0
        while True:
            try:
                async with websockets.connect(
                    f"{self._relay_url}/ws/daemon/{self.room}",
                    max_size=MAX_MESSAGE_LEN + 8,
                    ping_interval=20,
                ) as socket:
                    self._socket = socket
                    self.connected.set()
                    attempt = 0
                    _LOG.info("connected to the relay as room %s", self.room[:8])
                    await self._pump(socket)
            except asyncio.CancelledError:
                raise
            except (OSError, websockets.WebSocketException) as exc:
                _LOG.warning("relay connection failed: %s", exc)
            finally:
                self._socket = None
                self.connected.clear()
                sessions = list(self._sessions.values())
                self._sessions.clear()
                for session in sessions:
                    await session.close()
            await asyncio.sleep(self._delays[min(attempt, len(self._delays) - 1)])
            attempt += 1

    async def _pump(self, socket: Any) -> None:
        async for message in socket:
            try:
                if isinstance(message, str):
                    kind, client, _reason = parse_control(message)
                    if kind == CLIENT_OPEN:
                        self._sessions[client] = _Session(self, client)
                    elif kind == CLIENT_CLOSE:
                        session = self._sessions.pop(client, None)
                        if session is not None:
                            await session.close()
                else:
                    client, payload = unwrap(message)
                    session = self._sessions.get(client)
                    if session is not None:
                        await session.on_message(payload)
            except EnvelopeError as exc:
                _LOG.warning("relay sent something unexpected: %s", exc)


__all__ = [
    "RelayConnector",
    "load_or_create_daemon_key",
    "pairing_payload",
    "room_id_for",
]
