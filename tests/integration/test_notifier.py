"""CUT-312: a Needs you card, a limit and a stuck Autopilot reach a webhook (title, name)."""

from __future__ import annotations

import asyncio
import dataclasses
import http.server
import logging
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import ClassVar

import pytest

from cuttlefish.episodic.events import AutopilotUpdated
from cuttlefish.fleet.daemon import FleetDaemon
from cuttlefish.fleet.notifier import NOTIFY_URL_ENV, Notifier, configured_url
from cuttlefish.projects.store import ProjectStore

SECRET_PATH = "/topic-that-is-a-password"
Received = list[tuple[str, dict[str, str], str]]


class _Sink(http.server.BaseHTTPRequestHandler):
    received: ClassVar[Received] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        self.received.append((self.path, dict(self.headers), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def sink() -> Iterator[tuple[str, Received]]:
    _Sink.received = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}{SECRET_PATH}", _Sink.received
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
async def daemon(tmp_path: Path) -> AsyncIterator[tuple[FleetDaemon, str]]:
    store = ProjectStore.open(tmp_path / "projects.db")
    root = tmp_path / "alpha"
    root.mkdir()
    project = store.register(name="alpha", root=str(root), backend="claude-code")
    d = FleetDaemon(store, request_window_s=30)
    try:
        yield d, project.id
    finally:
        await d.close()
        store.close()


async def _until(received: Received, count: int) -> None:
    async with asyncio.timeout(5):
        while len(received) < count:
            await asyncio.sleep(0.02)


async def test_a_card_a_limit_and_a_stuck_autopilot_each_send_a_title_and_the_project_name(
    daemon: tuple[FleetDaemon, str], sink: tuple[str, Received]
) -> None:
    d, project_id = daemon
    url, received = sink
    d.notifier = Notifier(url)
    d.raise_decision(project_id, "Which database?", ["a", "b"], "a", role="builder")
    await _until(received, 1)
    path, headers, body = received[0]
    assert path == SECRET_PATH and headers["Title"] == "cuttlefish-crew"
    assert body.startswith("alpha: ")
    project = d.projects.get(project_id)
    base = AutopilotUpdated(phase="limit", goal="g", backend="claude-code")
    d.append_session_event(project, base)
    d.append_session_event(project, dataclasses.replace(base, phase="stuck"))
    d.append_session_event(project, dataclasses.replace(base, phase="running"))  # sends nothing
    await _until(received, 3)
    await asyncio.sleep(0.2)
    assert sorted(b for _, _, b in received[1:]) == [
        "alpha: Autopilot is stuck",
        "alpha: Autopilot reached a limit",
    ]


async def test_nothing_but_the_title_and_name_is_sent_and_the_url_is_never_logged(
    daemon: tuple[FleetDaemon, str],
    sink: tuple[str, Received],
    caplog: pytest.LogCaptureFixture,
) -> None:
    d, project_id = daemon
    url, received = sink
    d.notifier = Notifier(url)
    d.raise_decision(project_id, "Q? token=abc123secret", ["a", "b"], "a", role="builder")
    await _until(received, 1)
    assert "abc123secret" not in received[0][2] and "options" not in received[0][2]
    failing = Notifier("http://127.0.0.1:9" + SECRET_PATH)
    with caplog.at_level(logging.DEBUG):
        failing.notify("p", "alpha", "x", "t")
        await asyncio.sleep(0.5)
    assert "notification was not sent" in caplog.text and SECRET_PATH not in caplog.text


async def test_without_a_url_or_inside_the_quiet_minute_nothing_is_sent() -> None:
    sent: list[str] = []
    now = [0.0]
    off = Notifier(None, post=lambda *a: sent.append(a[2]), clock=lambda: now[0])
    assert not off.enabled and configured_url({NOTIFY_URL_ENV: "ftp://x/y"}) is None
    on = Notifier("http://x/y", post=lambda *a: sent.append(a[2]), clock=lambda: now[0])
    on.notify("p", "alpha", "stuck", "t")
    on.notify("p", "alpha", "stuck", "t")  # same project and reason within a minute
    now[0] = 61.0
    on.notify("p", "alpha", "stuck", "t")
    await asyncio.sleep(0.2)
    assert sent == ["alpha: t", "alpha: t"]
