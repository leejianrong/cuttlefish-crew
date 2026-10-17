"""The relay service (ADR-0037, CUT-119): a headless FastAPI app that pairs a daemon with the
browsers in its room and copies bytes between them.

It cannot read what it carries. A browser and the daemon run a Noise handshake through it, and every
message after the first is encrypted end to end. What it can see is which rooms exist, when a daemon
and each browser connect and leave, and how many bytes move and how often (see
`cuttlefish.relay.protocol.envelope` for the one thing it adds: a client id in front of each message
to the daemon).

**Open for now.** There are no accounts: a room is just a name that a daemon claims by connecting
first, and a second daemon connecting under the same name replaces the first. That is fine on
loopback and not acceptable in public, where accounts and a rate limit come with CUT-121. This
module must not import the fleet, secrets or episodic code (`tests/unit/relay/test_boundary.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

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


class RelayState:
    def __init__(self) -> None:
        self.rooms: dict[str, _Room] = {}

    def stats(self) -> dict[str, int]:
        return {
            "rooms": len(self.rooms),
            "daemons": sum(1 for room in self.rooms.values() if room.daemon is not None),
            "clients": sum(len(room.clients) for room in self.rooms.values()),
        }


def create_relay_app(state: RelayState | None = None) -> FastAPI:
    state = state or RelayState()
    app = FastAPI(title="cuttlefish-crew relay")
    app.state.relay = state

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, **state.stats()}

    @app.websocket("/ws/daemon/{room_id}")
    async def daemon_socket(socket: WebSocket, room_id: str) -> None:
        await socket.accept()
        peer = _Peer(socket)
        if not ROOM_PATTERN.match(room_id):
            await peer.close(CLOSE_BAD_ROOM, "bad room id")
            return
        if room_id not in state.rooms and len(state.rooms) >= MAX_ROOMS:
            await peer.close(CLOSE_FULL, "too many rooms")
            return
        room = state.rooms.setdefault(room_id, _Room())
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
                if state.rooms.get(room_id) is room:
                    del state.rooms[room_id]
            _LOG.info("daemon left room %s", room_id[:8])

    @app.websocket("/ws/client/{room_id}")
    async def client_socket(socket: WebSocket, room_id: str) -> None:
        await socket.accept()
        peer = _Peer(socket)
        room = state.rooms.get(room_id) if ROOM_PATTERN.match(room_id) else None
        if room is None or room.daemon is None:
            await peer.close(CLOSE_DAEMON_OFFLINE, "the daemon is offline")
            return
        if len(room.clients) >= MAX_CLIENTS_PER_ROOM:
            await peer.close(CLOSE_FULL, "too many browsers in this room")
            return
        client_id = room.next_client
        room.next_client += 1
        room.clients[client_id] = peer
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
                await daemon.send_bytes(wrap(client_id, data))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            room.clients.pop(client_id, None)
            if room.daemon is daemon:
                with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                    await daemon.send_text(control(CLIENT_CLOSE, client_id))

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


async def run_relay(host: str, port: int) -> None:
    # A Noise message is at most MAX_MESSAGE_LEN, plus the client id the relay adds going to the
    # daemon: nothing larger is ever legitimate, so nothing larger is read.
    config = uvicorn.Config(
        create_relay_app(),
        host=host,
        port=port,
        log_level="warning",
        ws_max_size=MAX_MESSAGE_LEN + 8,
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
