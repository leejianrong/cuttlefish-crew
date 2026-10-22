"""How long a round may run, and how often a team continues on its own (ADR-0030).

A round is one delegation, one fresh coding-agent session. It ends on a turn cap or a token
budget, and for a team meant to run for hours or days that is a checkpoint, not a failure: the
team writes a handover and starts the next round with a fresh context. These are the knobs;
the stuck-agent detector (ADR-0029) and the run's cost ceilings are what stop a runaway.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

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

#: Percent of the model's context window a round may fill before cuttlefish ends it and the role
#: continues from the handover: past about three quarters a model reads its history worse, and
#: the next request may not fit at all. Needs a kopicode that reports the window (v0.4.0).
DEFAULT_CONTEXT_LIMIT_PERCENT = 75

#: The ``failure_kind`` values that mean "the round ran out of room", not "it went wrong".
CHECKPOINT_STOPS = frozenset({"max_turns", "budget_exhausted", "round_timeout", "context_pressure"})


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


def context_limit_from_env(environ: Mapping[str, str] | None = None) -> float | None:
    """``CUTTLEFISH_CONTEXT_LIMIT_PERCENT`` as a fraction of the window: ``0`` turns it off, and
    a value over 95 is read as 95 (a round cannot be ended after the window is full)."""
    percent = _whole(
        "CUTTLEFISH_CONTEXT_LIMIT_PERCENT",
        DEFAULT_CONTEXT_LIMIT_PERCENT,
        minimum=0,
        environ=environ,
    )
    return min(percent, 95) / 100 if percent > 0 else None


# --- settings: a project's and a role's own limits ---------------------------------------------
#
# Every limit above is a global environment variable. A project (and a role in it) may set its
# own: role over project over the environment over the built-in default. Plain ``dict[str, int]``
# everywhere, so it journals, persists and travels as JSON; an absent key inherits.


class LimitsError(ValueError):
    """A limits mapping with an unknown key or a value out of range."""


@dataclass(frozen=True, slots=True)
class LimitSpec:
    """One setting: what the dashboard shows and the range the API accepts."""

    key: str
    title: str
    summary: str
    unit: str
    minimum: int
    maximum: int | None
    #: What a role or project that sets nothing gets right now (the environment, else built-in).
    effective: Callable[[], int]
    #: Meaning of ``0``, when it is more than the smallest value (``None``: it is just a number).
    zero_means: str | None = None


def _env_round_timeout_minutes() -> int:
    seconds = round_timeout_from_env()
    return 0 if seconds is None else max(round(seconds / 60), 1)


def _env_context_percent() -> int:
    fraction = context_limit_from_env()
    return 0 if fraction is None else round(fraction * 100)


LIMIT_SPECS: tuple[LimitSpec, ...] = (
    LimitSpec(
        "max_turns",
        "Turns per round",
        "How many model turns one round may take before it stops for room and the role "
        "continues from the handover.",
        "turns",
        1,
        None,
        max_turns_from_env,
    ),
    LimitSpec(
        "session_token_budget",
        "Tokens per round",
        "Tokens one round may spend, counting the history resent on every request, so it is "
        "far more than the window.",
        "tokens",
        0,
        None,
        session_token_budget_from_env,
        zero_means="no limit",
    ),
    LimitSpec(
        "context_limit_percent",
        "Context limit",
        "How full the model's context window may get before the round is ended and the role "
        "continues with a fresh one. Needs kopicode v0.4.0; an older one ignores it.",
        "% of the model's window",
        0,
        95,
        _env_context_percent,
        zero_means="off",
    ),
    LimitSpec(
        "round_timeout_minutes",
        "Time per round",
        "How long one round may run before cuttlefish stops it; the role continues from the "
        "handover. Time spent waiting for you counts.",
        "minutes",
        0,
        None,
        _env_round_timeout_minutes,
        zero_means="no limit",
    ),
    LimitSpec(
        "max_continuations",
        "Continuations",
        "How many times a role may carry on by itself after a round that ran out of room, "
        "in one run.",
        "continuations",
        0,
        None,
        max_continuations_from_env,
        zero_means="a round that runs out of room fails the role",
    ),
    LimitSpec(
        "max_idle_rounds",
        "Rounds with no change",
        "How many rounds in a row may use all their room without changing a file before the "
        "role is held for you.",
        "rounds",
        0,
        None,
        max_idle_rounds_from_env,
        zero_means="never held",
    ),
)

_SPEC_BY_KEY = {spec.key: spec for spec in LIMIT_SPECS}


def validate_limits(raw: object) -> dict[str, int]:
    """``raw`` as a limits mapping: only known keys, whole numbers in range. ``None`` values
    are dropped (they mean "inherit"). Raises :class:`LimitsError` naming the first problem."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise LimitsError("'limits' must be an object")
    found: dict[str, int] = {}
    for key, value in raw.items():
        spec = _SPEC_BY_KEY.get(key)
        if spec is None:
            raise LimitsError(f"unknown limit {key!r}; known: {', '.join(_SPEC_BY_KEY)}")
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool):
            raise LimitsError(f"{spec.title.lower()} must be a whole number")
        if value < spec.minimum or (spec.maximum is not None and value > spec.maximum):
            high = f" to {spec.maximum}" if spec.maximum is not None else " or more"
            raise LimitsError(f"{spec.title.lower()} must be {spec.minimum}{high}")
        found[key] = value
    return found


def merge_limits(*layers: Mapping[str, int] | None) -> dict[str, int]:
    """The layers laid over one another, later ones winning (project, then role)."""
    merged: dict[str, int] = {}
    for layer in layers:
        merged.update(layer or {})
    return merged


def max_turns_for(limits: Mapping[str, int] | None) -> int:
    return limits["max_turns"] if limits and "max_turns" in limits else max_turns_from_env()


def session_token_budget_for(limits: Mapping[str, int] | None) -> int:
    if limits and "session_token_budget" in limits:
        return limits["session_token_budget"]
    return session_token_budget_from_env()


def max_continuations_for(limits: Mapping[str, int] | None) -> int | None:
    """The role's own setting, or ``None`` to fall through to the team's and the environment's."""
    return limits.get("max_continuations") if limits else None


def max_idle_rounds_for(limits: Mapping[str, int] | None) -> int | None:
    return limits.get("max_idle_rounds") if limits else None


def round_timeout_for(limits: Mapping[str, int] | None) -> float | None:
    if limits and "round_timeout_minutes" in limits:
        minutes = limits["round_timeout_minutes"]
        return float(minutes * 60) if minutes > 0 else None
    return round_timeout_from_env()


def context_limit_for(limits: Mapping[str, int] | None) -> float | None:
    if limits and "context_limit_percent" in limits:
        percent = limits["context_limit_percent"]
        return min(percent, 95) / 100 if percent > 0 else None
    return context_limit_from_env()
