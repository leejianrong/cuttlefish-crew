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
#: How long a person's Stop waits for Codex to end a turn before leaving it to the round boundary.
_INTERRUPT_TIMEOUT = 20.0


@dataclasses.dataclass(eq=False)
class LiveTurn:
    """One running turn: how to send it a message, and the loop it runs on."""

    send: Callable[[str], Coroutine[Any, Any, object]]
    loop: asyncio.AbstractEventLoop
    #: Ends the turn (``turn/interrupt``) and waits for Codex to say it has ended.
    interrupt: Callable[[], Coroutine[Any, Any, object]] | None = None


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


async def interrupt_root(root: str) -> int:
    """End every running Codex turn under ``root`` (a person pressed Stop, CUT-166). Returns how
    many turns were asked. A turn Codex does not end in time is left to finish its round, as
    before: satay's cancel is still delivered at the boundary."""
    prefix = os.path.realpath(root)
    turns = [
        turn for (turn_root, _), turn in _live.items() if turn_root == prefix and turn.interrupt
    ]
    results = await asyncio.gather(
        *(_interrupt_one(turn) for turn in turns), return_exceptions=True
    )
    return sum(1 for result in results if result is True)


async def _interrupt_one(turn: LiveTurn) -> bool:
    assert turn.interrupt is not None
    try:
        future = asyncio.run_coroutine_threadsafe(turn.interrupt(), turn.loop)
        await asyncio.wait_for(asyncio.wrap_future(future), _INTERRUPT_TIMEOUT)
    except Exception as exc:
        _LOG.info("codex turn/interrupt was not confirmed: %s", exc)
        return False
    return True
