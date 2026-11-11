"""Shared fixtures for the relay integration tests."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from test_pairing_and_tiers import rig  # noqa: F401  (a fixture other modules here use)

from cuttlefish.worker.link import WorkerLink

_REPORT = re.compile(r"^/api/worker/teams/([^/]+)/(events|finish)$")


@pytest.fixture(autouse=True)
def _raw_worker_links_echo_their_dispatch(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A test that plays a worker by hand through a bare `WorkerLink` should not have to carry the
    dispatch token of every order by hand: remember the ones handed over and echo them on reports,
    as `worker/execute.py` does. A body that names a dispatch itself (a stale one, in the tests of
    that) is left alone."""
    seen: dict[str, str] = {}
    real = WorkerLink.request

    async def request(
        self: WorkerLink, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        status, data = await real(self, method, path, body)
        if path == "/api/worker/work" and isinstance(data, dict):
            for order in data.get("orders", []):
                seen[order["team_id"]] = order["dispatch"]
        return status, data

    async def report(
        self: WorkerLink, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        found = _REPORT.match(path)
        if found and body is not None and "dispatch" not in body:
            body = {**body, "dispatch": seen.get(found[1])}
        return await request(self, method, path, body)

    monkeypatch.setattr(WorkerLink, "request", report)
    yield
