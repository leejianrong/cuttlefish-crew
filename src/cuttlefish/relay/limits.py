"""Per-address limits for the hosted relay (ADR-0043, CUT-135).

Counters live in memory and are never written down: an address is used to refuse a flood and is
then forgotten. Behind a proxy the address is the one the proxy saw, and the relay believes
`X-Forwarded-For` only when it was told to (`trust_forwarded`) because the proxy is the only thing
that can reach it.

This module must not import the fleet, secrets or episodic code
(`tests/unit/relay/test_boundary.py`).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping

#: ADR-0043's defaults.
MAX_CONNECTIONS_PER_ADDRESS = 40
NEW_CONNECTIONS_PER_MINUTE = 60
SIGN_INS_PER_MINUTE = 10
DAEMON_AUTH_FAILURES_PER_MINUTE = 10


class RateWindow:
    """At most `limit` hits per key in any `window` seconds."""

    def __init__(
        self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> bool:
        """Record an attempt. False means it is over the limit and was not counted."""
        now = self._clock()
        hits = self._hits[key]
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True

    def over(self, key: str) -> bool:
        """Whether `key` is at its limit, without recording an attempt."""
        now = self._clock()
        hits = self._hits.get(key)
        if not hits:
            return False
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        return len(hits) >= self._limit

    def forget_idle(self) -> None:
        now = self._clock()
        for key in [
            k for k, hits in self._hits.items() if not hits or now - hits[-1] >= self._window
        ]:
            del self._hits[key]


class ConnectionCounter:
    """At most `limit` open connections per key."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._open: dict[str, int] = defaultdict(int)

    def acquire(self, key: str) -> bool:
        if self._open[key] >= self._limit:
            return False
        self._open[key] += 1
        return True

    def release(self, key: str) -> None:
        if self._open[key] > 0:
            self._open[key] -= 1
        if self._open[key] == 0:
            del self._open[key]

    def open_for(self, key: str) -> int:
        return self._open.get(key, 0)


def client_address(peer: str | None, headers: Mapping[str, str], *, trust_forwarded: bool) -> str:
    """The address to count a request against.

    With `trust_forwarded`, the last entry of `X-Forwarded-For`: the one the trusted proxy added is
    the end of the list, and anything a client put before it is not believed.
    """
    if trust_forwarded:
        forwarded = headers.get("x-forwarded-for", "")
        last = forwarded.rsplit(",", 1)[-1].strip()
        if last:
            return last
    return peer or "unknown"
