"""The credential broker: a loopback proxy that holds an agent's model API key so the agent never
does (ADR-0031, the "broker" ADR-0006 deferred).

An agent's own key used to sit in its process environment, one ``printenv`` away from any command
it ran. The broker keeps the real key here, in the daemon, and gives the agent a **lease** instead:
a base URL with a random token in its path, and the same token as the "key". A model call goes to
the broker, which looks the token up, swaps in the real key, and forwards the call to the one
upstream the lease names.

What a leaked lease is worth: it works only against this machine's loopback listener, only while
the lease is open (the caller revokes it when the delegation ends), and only for the one upstream
it was issued for. It is not the key, and it cannot be turned into the key.

What this does not do: stop an agent from *using* its lease while it runs (spending credit it was
already trusted to spend), or help a harness that signs in with a login file instead of a key
(those files are the harness's own and the broker never sees them).

Nothing here logs a body, a header, a token or a key: a line names the upstream, the project, the
method and the status. The broker is not a general proxy: it forwards to the fixed base URL of a
lease, never to a host the caller names.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Final, NamedTuple

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

_LOG = logging.getLogger(__name__)

LOOPBACK: Final = "127.0.0.1"
TOKEN_PREFIX: Final = "cfb_"

#: ``name -> (the upstream's real base URL, the header its key travels in)``. A header named
#: ``authorization`` carries ``Bearer <key>``.
UPSTREAMS: Final[dict[str, tuple[str, str]]] = {
    "anthropic": ("https://api.anthropic.com", "x-api-key"),
    "openai": ("https://api.openai.com/v1", "authorization"),
}

#: Request headers never forwarded: hop-by-hop ones, ones httpx sets itself, and every header an
#: agent could use to carry its own credential (the broker's own goes in after).
_DROP_REQUEST: Final = frozenset(
    {
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
        "transfer-encoding", "upgrade", "host", "content-length", "accept-encoding",
        "authorization", "x-api-key", "api-key", "anthropic-auth-token",
    }
)  # fmt: skip
#: Response headers not passed back: hop-by-hop, plus the two httpx's decoding makes untrue.
_DROP_RESPONSE: Final = frozenset(
    {
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
        "transfer-encoding", "upgrade", "content-length", "content-encoding",
    }
)  # fmt: skip

_METHODS: Final = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
_TIMEOUT: Final = httpx.Timeout(30.0, read=600.0)


class BrokerRoute(NamedTuple):
    """How a backend's own key is brokered: which upstream it talks to, the environment variable its
    key is read from (the lease's token takes that place), and the one its base URL is read from."""

    upstream: str
    key_env: str
    base_env: str


@dataclass(frozen=True)
class Lease:
    """What one delegation is given in place of a key. ``url`` and ``token`` are what an agent sees;
    ``key`` never leaves the broker (and is left out of ``repr``)."""

    token: str
    upstream: str
    base: str
    header: str
    url: str
    project: str | None = None
    key: str = field(default="", repr=False)


class Broker:
    """One loopback listener and the leases it answers for."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)
        self._owns_client = client is None
        self._leases: dict[str, Lease] = {}
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None
        self._port = 0

    @property
    def port(self) -> int:
        return self._port

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def active(self) -> int:
        """How many leases are open."""
        return len(self._leases)

    async def start(self) -> None:
        """Bind a loopback port (chosen by the OS, so nothing races for it) and start serving."""
        if self.running:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((LOOPBACK, 0))
        self._port = sock.getsockname()[1]
        app = Starlette(
            routes=[Route("/{upstream}/{token}/{rest:path}", self._forward, methods=_METHODS)]
        )
        config = uvicorn.Config(app, log_level="warning", lifespan="off")
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]))
        while not self._server.started:
            if self._task.done():
                self._task.result()
            await asyncio.sleep(0.01)
        _LOG.info("credential broker listening on %s:%s", LOOPBACK, self._port)

    async def close(self) -> None:
        """Stop serving and forget every lease."""
        self._leases.clear()
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self._server = None
        if self._owns_client:
            await self._client.aclose()

    def lease(
        self, upstream: str, key: str, *, base: str | None = None, project: str | None = None
    ) -> Lease:
        """Open a lease on `upstream` ("anthropic" or "openai") holding `key`. `base` replaces the
        upstream's usual base URL (an operator's own gateway, a test double); it is fixed for the
        lease's life."""
        if upstream not in UPSTREAMS:
            raise ValueError(f"unknown upstream {upstream!r}; expected one of {sorted(UPSTREAMS)}")
        if not key:
            raise ValueError("a lease needs a key to hold")
        if not self.running:
            raise RuntimeError("the broker is not running")
        default_base, header = UPSTREAMS[upstream]
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        lease = Lease(
            token=token,
            upstream=upstream,
            base=(base or default_base).rstrip("/"),
            header=header,
            url=f"http://{LOOPBACK}:{self._port}/{upstream}/{token}",
            project=project,
            key=key,
        )
        self._leases[token] = lease
        return lease

    def revoke(self, lease: Lease) -> None:
        """Close `lease`. Idempotent."""
        self._leases.pop(lease.token, None)

    async def _forward(self, request: Request) -> Response:
        upstream = request.path_params["upstream"]
        lease = self._leases.get(request.path_params["token"])
        if lease is None or lease.upstream != upstream:
            return PlainTextResponse("unknown or expired broker token", status_code=401)
        rest: str = request.path_params["rest"]
        if any(part in ("..", ".") or "\\" in part for part in rest.split("/")):
            return PlainTextResponse("refused path", status_code=400)
        url = f"{lease.base}/{rest}" + (f"?{request.url.query}" if request.url.query else "")
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST}
        headers[lease.header] = (
            f"Bearer {lease.key}" if lease.header == "authorization" else lease.key
        )
        body = await request.body()
        try:
            upstream_response = await self._client.send(
                self._client.build_request(request.method, url, headers=headers, content=body),
                stream=True,
            )
        except httpx.HTTPError as exc:
            _LOG.warning(
                "broker: %s %s for project %s: upstream unreachable (%s)",
                upstream, request.method, lease.project, type(exc).__name__,
            )  # fmt: skip
            return PlainTextResponse("upstream unreachable", status_code=502)
        _LOG.info(
            "broker: %s %s for project %s -> %s",
            upstream, request.method, lease.project, upstream_response.status_code,
        )  # fmt: skip

        async def body_stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream_response.aiter_bytes():
                    yield chunk
            finally:
                await upstream_response.aclose()

        return StreamingResponse(
            body_stream(),
            status_code=upstream_response.status_code,
            headers={
                k: v
                for k, v in upstream_response.headers.items()
                if k.lower() not in _DROP_RESPONSE
            },
        )
