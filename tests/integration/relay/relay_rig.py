"""A real relay, a real connector and a Python browser, all on loopback, for the CUT-119 tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from dataclasses import dataclass, field
from typing import Any

import uvicorn
import websockets
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cuttlefish.relay.protocol import (
    WS_ACCEPTED,
    WS_BINARY,
    WS_TEXT,
    Frame,
    FrameType,
    Initiator,
    Role,
    StreamMux,
    Transport,
    decode,
    encode,
    generate_static,
)
from cuttlefish.relay.server import create_relay_app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Server:
    """An ASGI app on a real port, started and stopped inside the test's event loop."""

    def __init__(self, app: ASGIApp, port: int | None = None) -> None:
        self.port = port or free_port()
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._server.serve())
        while not self._server.started:
            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        self._server.should_exit = True
        if self._task is not None:
            await self._task

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"


@dataclass
class Tap:
    """Everything the relay received and sent, as a middleware would see it."""

    seen: list[bytes] = field(default_factory=list)

    def wrap(self, app: ASGIApp) -> ASGIApp:
        async def tapped(scope: Scope, receive: Receive, send: Send) -> None:
            async def tap_receive() -> Message:
                message = await receive()
                for key in ("bytes", "text"):
                    value = message.get(key)
                    if value is not None:
                        self.seen.append(value if isinstance(value, bytes) else value.encode())
                return message

            async def tap_send(message: Message) -> None:
                for key in ("bytes", "text"):
                    value = message.get(key)
                    if value is not None:
                        self.seen.append(value if isinstance(value, bytes) else value.encode())
                await send(message)

            await app(scope, tap_receive, tap_send)

        return tapped


def relay_app(tap: Tap | None = None) -> ASGIApp:
    app: ASGIApp = create_relay_app()
    return tap.wrap(app) if tap else app


class BrowserSocket:
    def __init__(self, browser: Browser, stream: int) -> None:
        self._browser = browser
        self._stream = stream
        self.accepted = asyncio.Event()
        self.messages: asyncio.Queue[str | bytes | tuple[int, str]] = asyncio.Queue()

    async def send_text(self, text: str) -> None:
        await self._browser._send_stream(self._stream, bytes([WS_TEXT]) + text.encode())

    async def send_bytes(self, data: bytes) -> None:
        await self._browser._send_stream(self._stream, bytes([WS_BINARY]) + data)

    async def recv(self, timeout: float = 5) -> str | bytes | tuple[int, str]:
        return await asyncio.wait_for(self.messages.get(), timeout)

    async def close(self) -> None:
        await self._browser._close_stream(self._stream)


class Browser:
    """The browser's end of the protocol, in Python."""

    def __init__(self, socket: Any, transport: Transport) -> None:
        self._socket = socket
        self._transport = transport
        self._mux = StreamMux(Role.INITIATOR)
        self._lock = asyncio.Lock()
        self._frames: dict[int, asyncio.Queue[Frame]] = {}
        self._sockets: dict[int, BrowserSocket] = {}
        self._reader: asyncio.Task[None] | None = None
        self.closed = asyncio.Event()
        self.close_code: int | None = None

    @classmethod
    async def connect(cls, relay_url: str, room: str, daemon_public: bytes, psk: bytes) -> Browser:
        socket = await websockets.connect(f"{relay_url}/ws/client/{room}", max_size=70000)
        initiator = Initiator(generate_static(), daemon_public, psk)
        await socket.send(initiator.write_message1())
        reply = await asyncio.wait_for(socket.recv(), 5)
        assert isinstance(reply, bytes)
        transport, _ = initiator.read_message2(reply)
        browser = cls(socket, transport)
        await socket.send(transport.encrypt(encode(Frame(FrameType.PING, 0, b"hello"))))
        pong = decode(transport.decrypt(await asyncio.wait_for(socket.recv(), 5)))
        assert pong.type is FrameType.PONG
        browser._reader = asyncio.create_task(browser._read())
        return browser

    async def _read(self) -> None:
        try:
            async for message in self._socket:
                assert isinstance(message, bytes)
                frame = self._mux.receive(decode(self._transport.decrypt(message)))
                if frame.type is FrameType.PING:
                    await self._send(self._mux.pong_for(frame))
                    continue
                socket = self._sockets.get(frame.stream)
                if socket is not None:
                    self._on_socket_frame(socket, frame)
                else:
                    self._frames.setdefault(frame.stream, asyncio.Queue()).put_nowait(frame)
        except websockets.ConnectionClosed as exc:
            self.close_code = exc.rcvd.code if exc.rcvd else None
        finally:
            self.closed.set()

    def _on_socket_frame(self, socket: BrowserSocket, frame: Frame) -> None:
        if frame.type is FrameType.WS_DATA:
            kind, data = frame.payload[0], frame.payload[1:]
            if kind == WS_ACCEPTED:
                socket.accepted.set()
            elif kind == WS_TEXT:
                socket.messages.put_nowait(data.decode())
            elif kind == WS_BINARY:
                socket.messages.put_nowait(data)
        elif frame.type is FrameType.WS_CLOSE:
            code = int.from_bytes(frame.payload[:2], "big")
            socket.messages.put_nowait((code, frame.payload[2:].decode()))

    async def _send(self, *frames: Frame) -> None:
        async with self._lock:
            for frame in frames:
                await self._socket.send(self._transport.encrypt(encode(frame)))

    async def _send_stream(self, stream: int, body: bytes) -> None:
        await self._send(*self._mux.send(stream, body))

    async def _close_stream(self, stream: int) -> None:
        await self._send(self._mux.close(stream, (1000).to_bytes(2, "big")))

    async def request(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        head = {"method": method, "path": path, "headers": list((headers or {}).items())}
        stream, opening = self._mux.open(FrameType.REQUEST, json.dumps(head).encode())
        queue: asyncio.Queue[Frame] = self._frames.setdefault(stream, asyncio.Queue())
        await self._send(opening, *self._mux.send(stream, body), self._mux.close(stream))
        response = await asyncio.wait_for(queue.get(), 10)
        assert response.type is FrameType.RESPONSE, response
        head_out = json.loads(response.payload)
        chunks: list[bytes] = []
        while True:
            frame = await asyncio.wait_for(queue.get(), 10)
            if frame.type is FrameType.END:
                break
            assert frame.type is FrameType.DATA, frame
            chunks.append(frame.payload)
        return head_out["status"], dict(head_out["headers"]), b"".join(chunks)

    async def websocket(self, path: str) -> BrowserSocket:
        stream, opening = self._mux.open(FrameType.WS_OPEN, json.dumps({"path": path}).encode())
        socket = BrowserSocket(self, stream)
        self._sockets[stream] = socket
        await self._send(opening)
        return socket

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._socket.close()
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
