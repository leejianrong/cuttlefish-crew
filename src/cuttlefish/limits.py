"""How long a round may run, and how often a team continues on its own (ADR-0030).

A round is one delegation, one fresh coding-agent session. It ends on a turn cap or a token
budget, and for a team meant to run for hours or days that is a checkpoint, not a failure: the
team writes a handover and starts the next round with a fresh context. These are the knobs;
the stuck-agent detector (ADR-0029) and the run's cost ceilings are what stop a runaway.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: Turns one prompt may take. kopicode's own REPL default; its ``serve`` default is 20.
DEFAULT_MAX_TURNS = 100
#: Tokens (prompt + completion, history resent each request) one session may spend.
DEFAULT_SESSION_TOKEN_BUDGET = 5_000_000
#: Rounds a role may continue by itself after a checkpoint stop, per team run.
DEFAULT_MAX_CONTINUATIONS = 20

#: Wall-clock seconds one round may run before cuttlefish cancels it. A hung agent, or one that
#: spends hours inside its turn cap, is stopped and its role continues from the handover.
DEFAULT_ROUND_TIMEOUT_S = 2 * 3600
#: Auto-continued rounds in a row that may end with no file changed before the role is held
#: for a person: 100 turns of nothing is not progress.
DEFAULT_MAX_IDLE_ROUNDS = 3

#: The ``failure_kind`` values that mean "the round ran out of room", not "it went wrong".
CHECKPOINT_STOPS = frozenset({"max_turns", "budget_exhausted", "round_timeout"})


def _whole(name: str, default: int, *, minimum: int, environ: Mapping[str, str] | None) -> int:
    raw = (environ if environ is not None else os.environ).get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(int(raw), minimum)
    except ValueError:
        return default


def max_turns_from_env(environ: Mapping[str, str] | None = None) -> int:
    """``CUTTLEFISH_MAX_TURNS``: at least 1, else the default."""
    return _whole("CUTTLEFISH_MAX_TURNS", DEFAULT_MAX_TURNS, minimum=1, environ=environ)


def session_token_budget_from_env(environ: Mapping[str, str] | None = None) -> int:
    """``CUTTLEFISH_SESSION_TOKEN_BUDGET``: ``0`` is unbounded, else the default."""
    return _whole(
        "CUTTLEFISH_SESSION_TOKEN_BUDGET", DEFAULT_SESSION_TOKEN_BUDGET, minimum=0, environ=environ
    )


def max_continuations_from_env(environ: Mapping[str, str] | None = None) -> int:
    """``CUTTLEFISH_MAX_CONTINUATIONS``: ``0`` keeps a checkpoint stop a failed round."""
    return _whole(
        "CUTTLEFISH_MAX_CONTINUATIONS", DEFAULT_MAX_CONTINUATIONS, minimum=0, environ=environ
    )


def round_timeout_from_env(environ: Mapping[str, str] | None = None) -> float | None:
    """``CUTTLEFISH_ROUND_TIMEOUT`` in seconds: ``0`` for no limit, else the default."""
    seconds = _whole(
        "CUTTLEFISH_ROUND_TIMEOUT", DEFAULT_ROUND_TIMEOUT_S, minimum=0, environ=environ
    )
    return float(seconds) if seconds > 0 else None


def max_idle_rounds_from_env(environ: Mapping[str, str] | None = None) -> int:
    """``CUTTLEFISH_MAX_IDLE_ROUNDS``: ``0`` turns the no-progress stop off."""
    return _whole("CUTTLEFISH_MAX_IDLE_ROUNDS", DEFAULT_MAX_IDLE_ROUNDS, minimum=0, environ=environ)
