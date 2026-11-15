"""A push when the dashboard is not open: one POST to a webhook or ntfy topic (CUT-312).

The URL is the daemon's own setting, ``CUTTLEFISH_NOTIFY_URL`` in the environment of
``cuttlefish serve``. It is not in the secrets store on purpose: every shared secret is handed to
every project's agents, and an ntfy topic URL is a password for reading the feed. An agent's
environment is allowlisted (ADR-0029), so the daemon's does not reach it.

What is sent is a card's title and the project's name, nothing else: no command, no detail, no
answer. The text is the body, with a ``Title`` header that ntfy shows as the notification's title
and any webhook can read. The URL is never logged, and a failure logs the exception's class only.
Web Push through the relay would need its own ADR and is not this.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import urllib.request
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

NOTIFY_URL_ENV = "CUTTLEFISH_NOTIFY_URL"
#: One notice per project and reason in this long, so a flapping state is not a flood.
_QUIET_S = 60.0
_TIMEOUT_S = 10.0
_MAX_CHARS = 200


def _post(url: str, title: str, body: str) -> None:
    request = urllib.request.Request(
        url,
        data=body.encode(),
        headers={"Title": title, "Content-Type": "text/plain; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S):
        pass


def configured_url(environ: Mapping[str, str] | None = None) -> str | None:
    """The sink's URL, or ``None`` when unset or not an http(s) URL."""
    raw = (environ if environ is not None else os.environ).get(NOTIFY_URL_ENV, "").strip()
    parts = urlsplit(raw)
    return raw if parts.scheme in ("http", "https") and parts.netloc else None


class Notifier:
    def __init__(
        self,
        url: str | None = None,
        *,
        post: Callable[[str, str, str], None] = _post,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url if url is not None else configured_url()
        self._post = post
        self._clock = clock
        self._last: dict[tuple[str, str], float] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def enabled(self) -> bool:
        return self._url is not None

    def notify(self, project_id: str, project_name: str, reason: str, title: str) -> None:
        """Send ``title`` for ``project_name`` and return at once; ``reason`` only de-duplicates."""
        if self._url is None:
            return
        now = self._clock()
        last = self._last.get((project_id, reason))
        if last is not None and now - last < _QUIET_S:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._last[(project_id, reason)] = now
        task = loop.create_task(self._send(self._url, title[:_MAX_CHARS], project_name))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, url: str, title: str, project_name: str) -> None:
        try:
            await asyncio.to_thread(self._post, url, "cuttlefish-crew", f"{project_name}: {title}")
        except Exception as exc:
            logger.warning("a notification was not sent (%s)", type(exc).__name__)
