"""A browser's view of a project's tmux session, over a pty (CUT-96, ADR-0035).

``tmux attach`` is run on a pseudo-terminal this module owns; bytes the client types are written
to it and bytes tmux draws are read from it. That is what a terminal emulator in the browser
(xterm.js) speaks, so the real TUI works as it does in a terminal: colours, the cursor, a tool's
own permission prompt. Each browser tab is its own tmux client, so several can look at one
session and tmux sizes it to the smallest. Closing the view detaches the client; the agent keeps
running (ADR-0032).
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios
from collections.abc import AsyncIterator

from cuttlefish.sessions.tmux import Tmux

_CHUNK = 65536
_MIN_SIZE = 1
_MAX_SIZE = 1000


def _set_size(fd: int, cols: int, rows: int) -> None:
    cols = max(_MIN_SIZE, min(_MAX_SIZE, cols))
    rows = max(_MIN_SIZE, min(_MAX_SIZE, rows))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _become_controlling_terminal() -> None:
    # Runs in the child after `start_new_session` (setsid): make the pty on stdin the session's
    # controlling terminal, which `tmux attach` needs.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


class PtyAttach:
    """One attached tmux client. ``read()`` yields what it draws until it exits; ``write`` and
    ``resize`` are the other direction; ``close`` detaches."""

    def __init__(self, tmux: Tmux, session: str, *, cols: int = 120, rows: int = 32) -> None:
        self._master, slave = pty.openpty()
        _set_size(self._master, cols, rows)
        os.set_blocking(self._master, False)
        env = {
            "TERM": "xterm-256color",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/"),
            "LANG": "C.UTF-8",
        }
        try:
            self._process = subprocess.Popen(
                [
                    tmux.binary,
                    "-u",
                    "-S",
                    str(tmux.socket),
                    "attach-session",
                    "-t",
                    f"={session}",
                ],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                close_fds=True,
                start_new_session=True,
                preexec_fn=_become_controlling_terminal,
            )
        finally:
            os.close(slave)
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._loop = asyncio.get_running_loop()
        self._loop.add_reader(self._master, self._on_readable)
        self._closed = False

    def _on_readable(self) -> None:
        try:
            data = os.read(self._master, _CHUNK)
        except BlockingIOError:
            return
        except OSError:  # EIO: the child closed its side
            data = b""
        if data:
            self._queue.put_nowait(data)
        else:
            self._loop.remove_reader(self._master)
            self._queue.put_nowait(None)

    async def read(self) -> AsyncIterator[bytes]:
        while (chunk := await self._queue.get()) is not None:
            yield chunk

    async def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view and not self._closed:
            try:
                written = os.write(self._master, view)
            except BlockingIOError:
                await asyncio.sleep(0.005)
                continue
            except OSError:
                return
            view = view[written:]

    def resize(self, cols: int, rows: int) -> None:
        if not self._closed:
            with contextlib.suppress(OSError):
                _set_size(self._master, cols, rows)

    def alive(self) -> bool:
        return self._process.poll() is None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            with contextlib.suppress(ValueError, OSError):
                self._loop.remove_reader(self._master)
            if self._process.poll() is None:
                # SIGHUP makes the tmux client detach; the session itself is untouched.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self._process.pid, signal.SIGHUP)
                try:
                    await asyncio.wait_for(asyncio.to_thread(self._process.wait), 3)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(self._process.pid, signal.SIGKILL)
                    await asyncio.to_thread(self._process.wait)
        finally:
            # Also when this task is cancelled mid-wait (a server shutting down, a test client
            # tearing down): the pty must still be closed, and a client that has not detached yet
            # is killed rather than left behind. Only the tmux client dies, never the session.
            if self._process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self._process.pid, signal.SIGKILL)
            with contextlib.suppress(OSError):
                os.close(self._master)
            self._queue.put_nowait(None)
