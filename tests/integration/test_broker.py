"""Integration: the credential broker (ADR-0031) in front of a fake upstream that records what
reaches it."""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from cuttlefish.broker import LOOPBACK, TOKEN_PREFIX, Broker

KEY = "canary-real-key-do-not-leak"


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


@pytest.fixture
async def upstream() -> AsyncIterator[Upstream]:
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


@pytest.fixture
async def broker() -> AsyncIterator[Broker]:
    b = Broker()
    await b.start()
    yield b
    await b.close()


async def test_the_real_key_is_swapped_in_and_the_agents_own_credential_dropped(
    broker: Broker, upstream: Upstream
) -> None:
    lease = broker.lease("anthropic", KEY, base=upstream.base, project="p1")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{lease.url}/v1/messages?beta=true",
            headers={
                "x-api-key": lease.token,
                "api-key": "agents-own",
                "anthropic-version": "2023-06-01",
            },
            content=b'{"model":"m"}',
        )
    assert response.status_code == 200
    (seen,) = upstream.seen
    assert seen["path"] == "/api/v1/messages"
    assert seen["query"] == "beta=true"
    assert seen["body"] == '{"model":"m"}'
    assert seen["headers"]["x-api-key"] == KEY
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert "api-key" not in seen["headers"]
    assert lease.token not in str(seen["headers"])


async def test_an_openai_lease_sends_the_key_as_a_bearer_token(
    broker: Broker, upstream: Upstream
) -> None:
    lease = broker.lease("openai", KEY, base=upstream.base)
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{lease.url}/responses", headers={"authorization": f"Bearer {lease.token}"}
        )
    assert upstream.seen[0]["headers"]["authorization"] == f"Bearer {KEY}"


async def test_an_upstream_status_passes_through(broker: Broker, upstream: Upstream) -> None:
    lease = broker.lease("anthropic", KEY, base=upstream.base)
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{lease.url}/limited")
    assert response.status_code == 429


async def test_a_stream_arrives_as_it_is_produced(broker: Broker, upstream: Upstream) -> None:
    lease = broker.lease("anthropic", KEY, base=upstream.base)
    async with (
        httpx.AsyncClient() as client,
        client.stream("POST", f"{lease.url}/stream") as response,
    ):
        chunks = response.aiter_bytes()
        assert await asyncio.wait_for(anext(chunks), 5) == b"data: first\n\n"
        assert not upstream.release.is_set()  # the first chunk came before the upstream finished
        upstream.release.set()
        assert await asyncio.wait_for(anext(chunks), 5) == b"data: second\n\n"


async def test_an_unknown_or_revoked_token_is_refused_and_reaches_nothing(
    broker: Broker, upstream: Upstream
) -> None:
    lease = broker.lease("anthropic", KEY, base=upstream.base)
    async with httpx.AsyncClient() as client:
        assert (await client.get(f"{lease.url}/x")).status_code == 200
        broker.revoke(lease)
        broker.revoke(lease)  # idempotent
        assert (await client.get(f"{lease.url}/x")).status_code == 401
        forged = f"http://{LOOPBACK}:{broker.port}/anthropic/{TOKEN_PREFIX}nope/x"
        assert (await client.get(forged)).status_code == 401
    assert len(upstream.seen) == 1


async def test_a_token_is_good_for_its_own_upstream_only(
    broker: Broker, upstream: Upstream
) -> None:
    lease = broker.lease("anthropic", KEY, base=upstream.base)
    other = f"http://{LOOPBACK}:{broker.port}/openai/{lease.token}/x"
    async with httpx.AsyncClient() as client:
        assert (await client.get(other)).status_code == 401
    assert upstream.seen == []


async def test_a_path_that_climbs_is_refused(broker: Broker, upstream: Upstream) -> None:
    lease = broker.lease("anthropic", KEY, base=upstream.base)
    # httpx would normalise `..` away, so send the request line by hand
    reader, writer = await asyncio.open_connection(LOOPBACK, broker.port)
    path = lease.url.removeprefix(f"http://{LOOPBACK}:{broker.port}")
    writer.write(f"GET {path}/a/../b HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
    reply = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    assert reply.startswith(b"HTTP/1.1 400")
    assert upstream.seen == []


async def test_an_unreachable_upstream_is_a_502_that_names_no_key(
    broker: Broker, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    lease = broker.lease("anthropic", KEY, base=f"http://{LOOPBACK}:1")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{lease.url}/x")
    assert response.status_code == 502
    assert KEY not in response.text


async def test_nothing_secret_is_logged(
    broker: Broker, upstream: Upstream, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    lease = broker.lease("anthropic", KEY, base=upstream.base, project="p1")
    async with httpx.AsyncClient() as client:
        await client.post(f"{lease.url}/v1/messages", content=b"prompt text")
    assert "broker: anthropic POST for project p1 -> 200" in caplog.text
    assert KEY not in caplog.text
    assert "prompt text" not in caplog.text
    # the test client's own httpx logger names the URL it called, so look at the broker's lines only
    ours = " ".join(r.getMessage() for r in caplog.records if r.name == "cuttlefish.broker")
    assert lease.token not in ours


async def test_the_listener_is_loopback_only_and_a_lease_hides_its_key(broker: Broker) -> None:
    lease = broker.lease("openai", KEY)
    assert lease.url.startswith(f"http://{LOOPBACK}:{broker.port}/openai/{TOKEN_PREFIX}")
    assert KEY not in repr(lease)
    assert broker.active() == 1
    with pytest.raises(ValueError, match="unknown upstream"):
        broker.lease("nope", KEY)
    with pytest.raises(ValueError, match="needs a key"):
        broker.lease("openai", "")


async def test_closing_forgets_every_lease_and_stops_the_listener() -> None:
    b = Broker()
    await b.start()
    port = b.port
    b.lease("openai", KEY)
    await b.close()
    assert b.active() == 0 and not b.running
    with pytest.raises(RuntimeError, match="not running"):
        b.lease("openai", KEY)
    with pytest.raises(OSError), socket.create_connection((LOOPBACK, port), timeout=1):
        pass
