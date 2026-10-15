"""Codex turns that can be steered while they run (CUT-92, ADR-0033).

ADR-0008 made steering a round-boundary affair because no backend took input after it started.
Codex's ``app-server`` does: ``turn/steer`` adds a message to the turn in flight (verified live in
``docs/research/session-control-spike.md``). While a Codex turn runs, :func:`run_codex_app_server`
registers it here, keyed by the project root and the role, and the fleet daemon, which shares the
process (ADR-0009), asks :func:`steer` first. ``False`` means no turn is running or Codex refused
it, and the caller falls back to the queued, round-boundary steer.

The turn lives on the loop that ran the delegation, which need not be the caller's, so the send is
handed to that loop rather than awaited here.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from collections.abc import Callable, Coroutine
from typing import Any

_LOG = logging.getLogger(__name__)

#: How long Codex may take to accept a ``turn/steer`` before it is treated as refused.
_STEER_TIMEOUT = 10.0


@dataclasses.dataclass(eq=False)
class LiveTurn:
    """One running turn: how to send it a message, and the loop it runs on."""

    send: Callable[[str], Coroutine[Any, Any, object]]
    loop: asyncio.AbstractEventLoop


_live: dict[tuple[str, str], LiveTurn] = {}


def _key(root: str, role: str) -> tuple[str, str]:
    return os.path.realpath(root), role


def register(root: str, role: str, turn: LiveTurn) -> None:
    _live[_key(root, role)] = turn


def unregister(root: str, role: str, turn: LiveTurn) -> None:
    """Drop ``turn`` only if it is still the registered one, so a later turn is never removed."""
    if _live.get(_key(root, role)) is turn:
        del _live[_key(root, role)]


def is_live(root: str, role: str) -> bool:
    return _key(root, role) in _live


async def steer(root: str, role: str, text: str) -> bool:
    """Put ``text`` into ``role``'s running Codex turn. ``True`` only once Codex accepted it."""
    turn = _live.get(_key(root, role))
    if turn is None:
        return False
    try:
        future = asyncio.run_coroutine_threadsafe(turn.send(text), turn.loop)
        await asyncio.wait_for(asyncio.wrap_future(future), _STEER_TIMEOUT)
    except Exception as exc:
        _LOG.info("codex turn/steer for role %s was not accepted: %s", role, exc)
        return False
    return True
