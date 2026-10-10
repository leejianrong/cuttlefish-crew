"""A fake model API for the broker's tests: records what reaches it, streams on demand."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from cuttlefish.broker import LOOPBACK


@dataclass
class Upstream:
    port: int
    seen: list[dict[str, Any]] = field(default_factory=list)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def base(self) -> str:
        return f"http://{LOOPBACK}:{self.port}/api"


async def _serve(app: Starlette) -> tuple[uvicorn.Server, asyncio.Task[None], int]:
    sock = socket.socket()
    sock.bind((LOOPBACK, 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        await asyncio.sleep(0.01)
    return server, task, port


async def upstream_fixture() -> AsyncIterator[Upstream]:
    state = Upstream(port=0)

    async def record(request: Request) -> JSONResponse:
        state.seen.append(
            {
                "method": request.method,
                "path": request.url.path,
                "query": request.url.query,
                "headers": dict(request.headers),
                "body": (await request.body()).decode(),
            }
        )
        return JSONResponse({"ok": True}, status_code=429 if "limited" in request.url.path else 200)

    async def stream(request: Request) -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            yield b"data: first\n\n"
            await state.release.wait()
            yield b"data: second\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    app = Starlette(
        routes=[
            Route("/api/stream", stream, methods=["POST"]),
            Route("/api/{rest:path}", record, methods=["GET", "POST"]),
        ]
    )
    server, task, state.port = await _serve(app)
    yield state
    state.release.set()
    server.should_exit = True
    await task
