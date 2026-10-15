"""Unit: the pty bridge to a tmux session (CUT-96) -- type in, see output, resize, detach."""

from __future__ import annotations

import asyncio
import shutil
import stat
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from cuttlefish.sessions import PtyAttach, Tmux

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")


@pytest.fixture
async def session(tmp_path: Path) -> AsyncIterator[tuple[Tmux, str]]:
    tool = tmp_path / "echoer"
    tool.write_text('#!/bin/sh\necho READY\nwhile IFS= read -r line; do echo "pong: $line"; done\n')
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    tmux = Tmux(tmp_path / "t.sock")
    tmux.new_session(
        "cf-t", cwd=str(tmp_path), argv=[str(tool)], env={"PATH": "/usr/bin:/bin", "TERM": "xterm"}
    )
    yield tmux, "cf-t"
    tmux._run("kill-server", check=False)


async def _until(attach: PtyAttach, needle: bytes, seconds: float = 8) -> bytes:
    seen = b""

    async def pump() -> None:
        nonlocal seen
        async for chunk in attach.read():
            seen += chunk
            if needle in seen:
                return

    await asyncio.wait_for(pump(), seconds)
    return seen


async def test_what_tmux_draws_arrives_and_what_is_typed_reaches_the_agent(
    session: tuple[Tmux, str],
) -> None:
    tmux, name = session
    attach = PtyAttach(tmux, name, cols=100, rows=30)
    try:
        await _until(attach, b"READY")
        await attach.write(b"hello\r")
        await _until(attach, b"pong: hello")
    finally:
        await attach.close()


async def test_the_status_line_is_off_so_the_view_shows_only_the_agent(
    session: tuple[Tmux, str],
) -> None:
    tmux, name = session
    shown = tmux._run("show-options", "-t", name, "status").stdout.strip()
    assert shown == "status off"


async def test_closing_the_view_detaches_and_the_agent_keeps_running(
    session: tuple[Tmux, str],
) -> None:
    tmux, name = session
    attach = PtyAttach(tmux, name)
    await _until(attach, b"READY")
    await attach.close()
    assert not attach.alive()
    assert tmux.has_session(name)
    again = PtyAttach(tmux, name)
    try:
        await _until(again, b"READY")  # typing before tmux is attached would be flushed
        await again.write(b"still here\r")
        await _until(again, b"pong: still here")
    finally:
        await again.close()


async def test_a_resize_changes_the_size_tmux_sees(session: tuple[Tmux, str]) -> None:
    tmux, name = session
    attach = PtyAttach(tmux, name, cols=90, rows=25)
    try:
        await _until(attach, b"READY")
        attach.resize(70, 20)
        for _ in range(40):
            size = tmux._run(
                "list-clients", "-t", f"={name}", "-F", "#{client_width}x#{client_height}"
            )
            if size.stdout.strip() == "70x20":
                break
            await asyncio.sleep(0.1)
        assert size.stdout.strip() == "70x20"
    finally:
        await attach.close()


async def test_two_views_of_one_session_both_see_its_output(session: tuple[Tmux, str]) -> None:
    tmux, name = session
    a, b = PtyAttach(tmux, name), PtyAttach(tmux, name)
    try:
        await _until(a, b"READY")
        await _until(b, b"READY")
        await a.write(b"from a\r")
        await _until(a, b"pong: from a")
        await _until(b, b"pong: from a")
    finally:
        await a.close()
        await b.close()
