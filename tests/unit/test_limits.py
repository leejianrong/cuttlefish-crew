"""Unit: the round-limit settings read from the environment (ADR-0030)."""

from __future__ import annotations

import pytest

from cuttlefish.limits import (
    CHECKPOINT_STOPS,
    max_continuations_from_env,
    max_idle_rounds_from_env,
    max_turns_from_env,
    round_timeout_from_env,
    session_token_budget_from_env,
)


@pytest.mark.parametrize(
    ("raw", "turns"), [(None, 100), ("", 100), ("40", 40), ("0", 1), ("-3", 1), ("lots", 100)]
)
def test_max_turns(raw: str | None, turns: int) -> None:
    env = {} if raw is None else {"CUTTLEFISH_MAX_TURNS": raw}
    assert max_turns_from_env(env) == turns


@pytest.mark.parametrize(
    ("raw", "budget"), [(None, 5_000_000), ("0", 0), ("900000", 900000), ("x", 5_000_000)]
)
def test_session_token_budget_zero_is_unbounded(raw: str | None, budget: int) -> None:
    env = {} if raw is None else {"CUTTLEFISH_SESSION_TOKEN_BUDGET": raw}
    assert session_token_budget_from_env(env) == budget


@pytest.mark.parametrize(("raw", "count"), [(None, 20), ("0", 0), ("5", 5), ("-1", 0), ("x", 20)])
def test_max_continuations_zero_is_off(raw: str | None, count: int) -> None:
    env = {} if raw is None else {"CUTTLEFISH_MAX_CONTINUATIONS": raw}
    assert max_continuations_from_env(env) == count


def test_only_running_out_of_room_is_a_checkpoint() -> None:
    assert {"max_turns", "budget_exhausted", "round_timeout"} == CHECKPOINT_STOPS


@pytest.mark.parametrize(
    ("raw", "seconds"), [(None, 7200.0), ("0", None), ("90", 90.0), ("-5", None), ("x", 7200.0)]
)
def test_round_timeout_zero_is_no_limit(raw: str | None, seconds: float | None) -> None:
    env = {} if raw is None else {"CUTTLEFISH_ROUND_TIMEOUT": raw}
    assert round_timeout_from_env(env) == seconds


@pytest.mark.parametrize(("raw", "count"), [(None, 3), ("0", 0), ("5", 5), ("x", 3)])
def test_max_idle_rounds_zero_is_off(raw: str | None, count: int) -> None:
    env = {} if raw is None else {"CUTTLEFISH_MAX_IDLE_ROUNDS": raw}
    assert max_idle_rounds_from_env(env) == count
