"""A project's docker compose stack on a worker machine, as the controller tracks it (CUT-216).

A person starts the stack; the controller hands the worker an order on its next poll (like a team's
order, `placement.py`), and the worker runs `docker compose up -d` in the project's checkout and
reports which host ports the stack published. Nothing here reaches into the worker: a worker dials
out, so the controller cannot connect in either. Reaching the stack's web port is the same shape
turned around: a request for a page is queued for that worker, the worker fetches it from its own
loopback and posts the answer back, and the person's request is answered with it (`fetch`).

Only a port the stack itself published, and only on the worker's own loopback, can be asked for, and
the answer is served under a sandboxing policy so the stack's page cannot reach the dashboard's
origin or its token. The state is in memory: after a controller restart a stack is "not known" until
it is started or stopped again, and the containers keep running on the worker meanwhile
(`known-gaps.md`).
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: A stack order not reported on is handed over again after this long (the worker was restarted, or
#: the order was lost with a dropped link); `compose up` and `down` are safe to repeat.
RESEND_AFTER_SECONDS = 90.0
#: How long a person's request for a page waits for the worker.
FETCH_TIMEOUT_SECONDS = 30.0
#: The largest page the worker will send back.
MAX_BODY_BYTES = 1_000_000
_MAX_PORTS = 20
_STATES = ("up", "down", "failed")
_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]{0,1000}$")


class StackError(Exception):
    """What a person asked of a stack cannot be done; the message says why."""


@dataclass(slots=True)
class Stack:
    worker_id: str
    name: str
    #: `up` or `down`: what the person asked for last.
    want: str
    #: `starting`, `up`, `stopping`, `down` or `failed`.
    state: str
    ports: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    #: Where the worker clones the project from when it has no checkout yet.
    repo: str | None = None
    sent_at: float | None = None
    at: float = 0.0


@dataclass(slots=True)
class Page:
    status: int
    content_type: str
    body: bytes


@dataclass(slots=True)
class _Fetch:
    id: str
    worker_id: str
    project_id: str
    port: int
    path: str
    handed: bool = False
    future: asyncio.Future[Page] | None = None


def _clean(text: object, limit: int) -> str:
    raw = text if isinstance(text, str) else ""
    return "".join(ch for ch in raw if ch.isprintable()).strip()[:limit]


def clean_ports(raw: object) -> list[dict[str, Any]]:
    """The published ports a worker reports: a service name and two port numbers, nothing else."""
    found: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return found
    for item in raw[:_MAX_PORTS]:
        if not isinstance(item, dict):
            continue
        target, published = item.get("target"), item.get("published")
        if all(
            isinstance(n, int) and not isinstance(n, bool) and 0 < n < 65536
            for n in (target, published)
        ):
            found.append(
                {
                    "service": _clean(item.get("service"), 40),
                    "target": target,
                    "published": published,
                }
            )
    return found


class StackBoard:
    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._now = now
        self._stacks: dict[str, Stack] = {}  # by project id
        self._fetches: dict[str, _Fetch] = {}

    def status(self, project_id: str) -> dict[str, Any] | None:
        stack = self._stacks.get(project_id)
        if stack is None:
            return None
        return {
            "worker": stack.name,
            "state": stack.state,
            "ports": stack.ports,
            "error": stack.error,
            "at": stack.at,
        }

    def request(
        self, project_id: str, worker_id: str, name: str, repo: str | None, op: str
    ) -> None:
        """Ask for the stack `up` or `down`; the worker is told on its next poll."""
        previous = self._stacks.get(project_id)
        ports = previous.ports if previous and op == "down" else []
        self._stacks[project_id] = Stack(
            worker_id, name, op, "starting" if op == "up" else "stopping", ports, "", repo,
            None, self._now(),
        )  # fmt: skip

    def active(self, project_id: str, worker_id: str | None = None) -> bool:
        """Is there a stack that is, or may be, running for this project (on `worker_id`)?"""
        stack = self._stacks.get(project_id)
        return stack is not None and stack.state != "down" and worker_id in (None, stack.worker_id)

    def forget_worker(self, worker_id: str) -> None:
        """A worker was removed: what ran there is not ours to track any more."""
        for project_id in [p for p, s in self._stacks.items() if s.worker_id == worker_id]:
            del self._stacks[project_id]
        for pending in list(self._fetches.values()):
            if pending.worker_id == worker_id:
                self._finish(pending, Page(502, "text/plain", b"the worker was removed"))

    def claim(self, worker_id: str) -> dict[str, Any]:
        """The stack orders and page requests waiting for this worker, each handed over once."""
        now = self._now()
        orders: list[dict[str, Any]] = []
        for project_id, stack in self._stacks.items():
            if stack.worker_id != worker_id or stack.state not in ("starting", "stopping"):
                continue
            if stack.sent_at is not None and now - stack.sent_at < RESEND_AFTER_SECONDS:
                continue
            stack.sent_at = now
            orders.append(
                {"op": stack.want, "project_id": project_id, "name": stack.name, "repo": stack.repo}
            )
        pages: list[dict[str, Any]] = []
        for pending in self._fetches.values():
            if pending.worker_id == worker_id and not pending.handed:
                pending.handed = True
                pages.append(
                    {
                        "id": pending.id,
                        "project_id": pending.project_id,
                        "port": pending.port,
                        "path": pending.path,
                    }
                )
        return {"stacks": orders, "pages": pages} if orders or pages else {}

    def has_work(self, worker_id: str) -> bool:
        """Is something waiting to be handed to this worker (so a long poll can return)?"""
        now = self._now()
        return any(
            s.worker_id == worker_id
            and s.state in ("starting", "stopping")
            and (s.sent_at is None or now - s.sent_at >= RESEND_AFTER_SECONDS)
            for s in self._stacks.values()
        ) or any(f.worker_id == worker_id and not f.handed for f in self._fetches.values())

    def report(
        self, worker_id: str, project_id: str, state: object, ports: object, error: object
    ) -> None:
        stack = self._stacks.get(project_id)
        if stack is None or stack.worker_id != worker_id:
            raise StackError("this controller has no stack for that project on you")
        if state not in _STATES:
            raise StackError("'state' must be up, down or failed")
        stack.state = str(state)
        stack.ports = clean_ports(ports) if state == "up" else []
        stack.error = _clean(error, 300) if state == "failed" else ""
        stack.at = self._now()

    async def fetch(self, project_id: str, port: int, path: str) -> Page:
        """A page from the stack's published `port`, fetched by its worker."""
        stack = self._stacks.get(project_id)
        if stack is None or stack.state != "up":
            raise StackError("the stack is not running; start it first")
        if port not in {p["published"] for p in stack.ports}:
            raise StackError(f"the stack published no port {port}")
        if not _PATH.match(path) or ".." in path.split("?")[0].split("/"):
            raise StackError("that is not a path this can fetch")
        pending = _Fetch(
            uuid.uuid4().hex, stack.worker_id, project_id, port, path,
            future=asyncio.get_running_loop().create_future(),
        )  # fmt: skip
        self._fetches[pending.id] = pending
        try:
            assert pending.future is not None
            return await asyncio.wait_for(pending.future, FETCH_TIMEOUT_SECONDS)
        except TimeoutError:
            return Page(504, "text/plain", b"the worker did not answer in time")
        finally:
            self._fetches.pop(pending.id, None)

    def answer(
        self, worker_id: str, fetch_id: str, status: object, content_type: object, body: bytes
    ) -> None:
        pending = self._fetches.get(fetch_id)
        if pending is None or pending.worker_id != worker_id:
            raise StackError("no such request on you")
        code = status if isinstance(status, int) and 100 <= status < 600 else 502
        kind = _clean(content_type, 100) or "application/octet-stream"
        self._finish(pending, Page(code, kind, body[:MAX_BODY_BYTES]))

    def _finish(self, pending: _Fetch, page: Page) -> None:
        if pending.future is not None and not pending.future.done():
            pending.future.set_result(page)
