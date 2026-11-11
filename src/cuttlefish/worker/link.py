"""The worker's end of the encrypted channel to its controller (ADR-0057, ADR-0037).

A worker dials out, so this is the initiator side of `cuttlefish.relay.protocol`, the same one a
browser speaks, in Python: a Noise handshake with the controller's key and a psk (the one-time
pairing secret the first time, its own per-worker secret after), a first message the controller
must decrypt, then HTTP-shaped requests the controller answers from its own app, held to the
worker tier. No second protocol.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from dataclasses import dataclass
from typing import Any

import websockets
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from cuttlefish.relay.protocol import (
    Frame,
    FrameType,
    Initiator,
    NoiseError,
    Role,
    StreamError,
    StreamMux,
    Transport,
    decode,
    encode,
)

_TIMEOUT = 15.0


class LinkError(RuntimeError):
    """The channel could not be made, or broke."""


class JoinLinkError(ValueError):
    """What `cuttlefish worker join` was given is not a join code."""


@dataclass(frozen=True, slots=True)
class Controller:
    """Where the controller is and which key it holds (public)."""

    relay: str
    room: str
    daemon: bytes


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def parse_join_code(text: str) -> tuple[Controller, bytes]:
    """The controller and the one-time pairing secret from what the dashboard showed. A pasted
    link's `#pair=` is accepted too."""
    code = text.strip().rpartition("#pair=")[2]
    try:
        body = json.loads(unb64(code))
        return Controller(str(body["relay"]), str(body["room"]), unb64(body["daemon"])), unb64(
            body["pairing"]
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise JoinLinkError(
            "that is not a join code; copy the whole command from Workers on the dashboard"
        ) from exc


class WorkerLink:
    def __init__(self, socket: Any, transport: Transport) -> None:
        self._socket = socket
        self._transport = transport
        self._mux = StreamMux(Role.INITIATOR)
        self._lock = asyncio.Lock()
        self._frames: dict[int, asyncio.Queue[Frame]] = {}
        self.controls: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.closed = asyncio.Event()
        self._reader = asyncio.create_task(self._read())

    @classmethod
    async def open(
        cls,
        controller: Controller,
        static: X25519PrivateKey,
        psk: bytes,
        hello: dict[str, str],
    ) -> WorkerLink:
        """Handshake and send the first message. Nothing is trusted yet: the controller serves a
        paired worker once that message decrypts, and keeps a new one waiting for its owner."""
        try:
            socket = await asyncio.wait_for(
                websockets.connect(
                    f"{controller.relay.rstrip('/')}/ws/client/{controller.room}", max_size=70000
                ),
                _TIMEOUT,
            )
        except (OSError, TimeoutError, websockets.WebSocketException) as exc:
            raise LinkError(f"could not reach {controller.relay}: {exc}") from exc
        try:
            initiator = Initiator(static, controller.daemon, psk)
            await socket.send(initiator.write_message1())
            reply = await asyncio.wait_for(socket.recv(), _TIMEOUT)
            if not isinstance(reply, bytes):
                raise LinkError("the controller sent something unexpected")
            transport, _ = initiator.read_message2(reply)
            await socket.send(
                transport.encrypt(encode(Frame(FrameType.PING, 0, json.dumps(hello).encode())))
            )
        except (
            OSError,
            TimeoutError,
            websockets.WebSocketException,
            ValueError,
            NoiseError,
        ) as exc:
            await socket.close()
            raise LinkError(f"the controller did not accept the connection: {exc}") from exc
        return cls(socket, transport)

    async def _read(self) -> None:
        try:
            async for message in self._socket:
                frame = self._mux.receive(decode(self._transport.decrypt(message)))
                if frame.type is FrameType.PING:
                    await self._send(self._mux.pong_for(frame))
                elif frame.type is FrameType.CONTROL:
                    self.controls.put_nowait(json.loads(frame.payload))
                elif frame.type is not FrameType.PONG:
                    self._frames.setdefault(frame.stream, asyncio.Queue()).put_nowait(frame)
        except (websockets.WebSocketException, ValueError, StreamError, NoiseError):
            pass  # a broken or closed channel; `closed` tells the caller
        finally:
            self.closed.set()

    async def _send(self, *frames: Frame) -> None:
        async with self._lock:
            for frame in frames:
                await self._socket.send(self._transport.encrypt(encode(frame)))

    async def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        """One request to the controller's app: the status and the decoded JSON body."""
        head = {"method": method, "path": path, "headers": [["content-type", "application/json"]]}
        stream, opening = self._mux.open(FrameType.REQUEST, json.dumps(head).encode())
        queue: asyncio.Queue[Frame] = self._frames.setdefault(stream, asyncio.Queue())
        payload = json.dumps(body or {}).encode()
        try:
            await self._send(opening, *self._mux.send(stream, payload), self._mux.close(stream))
            response = await asyncio.wait_for(queue.get(), _TIMEOUT)
            if response.type is not FrameType.RESPONSE:
                raise LinkError("the controller reset the request")
            status = int(json.loads(response.payload)["status"])
            chunks: list[bytes] = []
            while (frame := await asyncio.wait_for(queue.get(), _TIMEOUT)).type is FrameType.DATA:
                chunks.append(frame.payload)
        except (TimeoutError, StreamError, websockets.WebSocketException) as exc:
            raise LinkError(f"the controller did not answer {path}") from exc
        finally:
            self._frames.pop(stream, None)
        data = b"".join(chunks)
        return status, json.loads(data) if data else None

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._socket.close()
        self._reader.cancel()
        await asyncio.gather(self._reader, return_exceptions=True)
