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

#: The ``failure_kind`` values that mean "the round ran out of room", not "it went wrong".
CHECKPOINT_STOPS = frozenset({"max_turns", "budget_exhausted"})


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
