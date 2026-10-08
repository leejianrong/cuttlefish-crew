"""Unit: the round-limit settings read from the environment (ADR-0030)."""

from __future__ import annotations

import pytest

from cuttlefish.limits import (
    CHECKPOINT_STOPS,
    max_continuations_from_env,
    max_turns_from_env,
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
    assert {"max_turns", "budget_exhausted"} == CHECKPOINT_STOPS
