"""A project's docker compose stack, run on this worker machine (CUT-216, ADR-0057).

The controller orders it (`fleet/stacks.py`); this module runs `docker compose` in the project's
checkout and says what happened: `up` with the host ports the stack published, `down`, or `failed`
with what compose said. It also fetches a page from one of those ports, from this machine's own
loopback, when the controller passes on a request for it. Nothing here opens a port to the world:
a compose file's `ports:` decides what the stack binds, and only a published port the stack itself
reports can be fetched.

It runs the project's own compose file with this machine's docker, so a project that mounts `/` or
runs privileged gets that here: the trust is the one a worker already has with a project's code.
Volumes are kept on `down`, so a database survives a stop.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
UP_TIMEOUT_SECONDS = 900.0
DOWN_TIMEOUT_SECONDS = 300.0
PAGE_TIMEOUT_SECONDS = 20.0
MAX_PAGE_BYTES = 1_000_000

Post = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
#: Runs `docker ...` in a directory: `(return code, stdout, stderr)`.
Docker = Callable[[list[str], Path, float], Awaitable[tuple[int, str, str]]]


async def run_docker(args: list[str], cwd: Path, timeout: float) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return 127, "", f"cannot run docker: {exc}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return 124, "", f"docker compose did not finish in {int(timeout)} seconds"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def project_name(project_id: str) -> str:
    return "cuttlefish-" + (re.sub(r"[^a-z0-9_-]", "", project_id.lower())[:40] or "project")


def compose_file(root: Path) -> Path | None:
    return next((root / n for n in COMPOSE_FILES if (root / n).is_file()), None)


def parse_ports(text: str) -> list[dict[str, Any]]:
    """The published tcp ports in `docker compose ps --format json` (one object per line, or one
    array, depending on the compose version), each once, as `{service, target, published}`."""
    rows: list[Any] = []
    stripped = text.strip()
    try:
        loaded = json.loads(stripped) if stripped.startswith("[") else None
    except ValueError:
        loaded = None
    if isinstance(loaded, list):
        rows = loaded
    else:
        for line in stripped.splitlines():
            with contextlib.suppress(ValueError):
                rows.append(json.loads(line))
    found: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for item in row.get("Publishers") or []:
            if not isinstance(item, dict) or item.get("Protocol", "tcp") != "tcp":
                continue
            published, target = item.get("PublishedPort"), item.get("TargetPort")
            if isinstance(published, int) and published > 0 and isinstance(target, int):
                found.setdefault(
                    published,
                    {
                        "service": str(row.get("Service") or ""),
                        "target": target,
                        "published": published,
                    },
                )
    return sorted(found.values(), key=lambda p: p["published"])


class StackRunner:
    def __init__(
        self, workdir: Path, say: Callable[[str], None], post: Post, docker: Docker = run_docker
    ) -> None:
        self._workdir, self._say, self._post, self._docker = workdir, say, post, docker
        self._busy: set[str] = set()
        self._ports: dict[str, set[int]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def handle(self, order: dict[str, Any]) -> None:
        """Start an order from the controller, unless one for the project is already going."""
        project_id = str(order.get("project_id") or "")
        if not project_id or project_id in self._busy or order.get("op") not in ("up", "down"):
            return
        self._busy.add(project_id)
        self._spawn(self._order(project_id, order))

    def serve(self, page: dict[str, Any]) -> None:
        self._spawn(self._page(page))

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _order(self, project_id: str, order: dict[str, Any]) -> None:
        name = str(order.get("name") or "a project")
        try:
            if order["op"] == "up":
                state, ports, error = await self._up(project_id, order)
            else:
                state, ports, error = await self._down(project_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state, ports, error = "failed", [], f"{type(exc).__name__}: {exc}"[:300]
        finally:
            self._busy.discard(project_id)
        self._say(f"Stack for {name}: {state}.")
        with contextlib.suppress(Exception):  # the controller sends the order again if this is lost
            await self._post(
                "/api/worker/stack",
                {"project_id": project_id, "state": state, "ports": ports, "error": error},
            )

    async def _up(
        self, project_id: str, order: dict[str, Any]
    ) -> tuple[str, list[dict[str, Any]], str]:
        from cuttlefish.worker.execute import checkout  # `execute` imports this module

        root = await asyncio.to_thread(
            checkout, self._workdir, project_id, order.get("repo") or None
        )
        file = compose_file(root)
        if file is None:
            return "failed", [], f"no compose file in the project ({', '.join(COMPOSE_FILES[:2])})"
        base = ["compose", "-p", project_name(project_id), "-f", str(file)]
        code, _out, err = await self._docker(
            [*base, "up", "-d", "--remove-orphans"], root, UP_TIMEOUT_SECONDS
        )
        if code != 0:
            return "failed", [], err.strip()[-300:] or f"docker compose up exited {code}"
        ports = await self._published(project_id, root, file)
        return "up", ports, ""

    async def _published(self, project_id: str, root: Path, file: Path) -> list[dict[str, Any]]:
        base = ["compose", "-p", project_name(project_id), "-f", str(file)]
        code, out, _err = await self._docker([*base, "ps", "--format", "json"], root, 60.0)
        ports = parse_ports(out) if code == 0 else []
        self._ports[project_id] = {p["published"] for p in ports}
        return ports

    async def _down(self, project_id: str) -> tuple[str, list[dict[str, Any]], str]:
        from cuttlefish.worker.execute import checkout_path

        root = checkout_path(self._workdir, project_id)
        cwd = root if root.is_dir() else self._workdir
        cwd.mkdir(parents=True, exist_ok=True)
        code, _out, err = await self._docker(
            ["compose", "-p", project_name(project_id), "down", "--remove-orphans"],
            cwd,
            DOWN_TIMEOUT_SECONDS,
        )
        self._ports.pop(project_id, None)
        if code != 0:
            return "failed", [], err.strip()[-300:] or f"docker compose down exited {code}"
        return "down", [], ""

    async def _page(self, page: dict[str, Any]) -> None:
        request_id, project_id = str(page.get("id") or ""), str(page.get("project_id") or "")
        port, path = page.get("port"), str(page.get("path") or "/")
        status, kind, body = 502, "text/plain", b"could not fetch that page"
        try:
            from cuttlefish.worker.execute import checkout_path

            known = self._ports.get(project_id)
            if known is None:  # this process restarted since the stack came up: ask compose
                root = checkout_path(self._workdir, project_id)
                file = compose_file(root) if root.is_dir() else None
                known = (
                    {p["published"] for p in await self._published(project_id, root, file)}
                    if file
                    else set()
                )
            if isinstance(port, int) and port in known and path.startswith("/"):
                async with (
                    httpx.AsyncClient(
                        timeout=PAGE_TIMEOUT_SECONDS, follow_redirects=False
                    ) as client,
                    client.stream("GET", f"http://127.0.0.1:{port}{path}") as response,
                ):
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > MAX_PAGE_BYTES:
                            break
                    status = response.status_code
                    kind = response.headers.get("content-type", "application/octet-stream")
                    body = b"".join(chunks)[:MAX_PAGE_BYTES]
            else:
                status, body = 404, b"that port is not one the stack published"
        except (httpx.HTTPError, OSError) as exc:
            body = f"could not reach the stack: {type(exc).__name__}".encode()
        with contextlib.suppress(Exception):
            await self._post(
                f"/api/worker/stack/pages/{request_id}",
                {"status": status, "content_type": kind, "body": base64.b64encode(body).decode()},
            )
