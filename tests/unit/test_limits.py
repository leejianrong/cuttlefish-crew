"""Unit: the round-limit settings read from the environment (ADR-0030)."""

from __future__ import annotations

import pytest

from cuttlefish.limits import (
    CHECKPOINT_STOPS,
    LimitsError,
    context_limit_for,
    context_limit_from_env,
    max_continuations_for,
    max_continuations_from_env,
    max_idle_rounds_for,
    max_idle_rounds_from_env,
    max_turns_for,
    max_turns_from_env,
    merge_limits,
    round_timeout_for,
    round_timeout_from_env,
    session_token_budget_for,
    session_token_budget_from_env,
    validate_limits,
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
    assert {
        "max_turns",
        "budget_exhausted",
        "round_timeout",
        "context_pressure",
    } == CHECKPOINT_STOPS


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


@pytest.mark.parametrize(
    ("raw", "fraction"), [(None, 0.75), ("0", None), ("60", 0.6), ("100", 0.95), ("x", 0.75)]
)
def test_context_limit_is_a_fraction_of_the_window_and_zero_is_off(
    raw: str | None, fraction: float | None
) -> None:
    env = {} if raw is None else {"CUTTLEFISH_CONTEXT_LIMIT_PERCENT": raw}
    assert context_limit_from_env(env) == fraction


def test_validate_keeps_known_whole_numbers_in_range_and_drops_nulls() -> None:
    assert validate_limits(None) == {}
    assert validate_limits({"max_turns": 5, "max_idle_rounds": None}) == {"max_turns": 5}
    assert validate_limits({"session_token_budget": 0, "context_limit_percent": 95}) == {
        "session_token_budget": 0,
        "context_limit_percent": 95,
    }


@pytest.mark.parametrize(
    "raw",
    [
        {"max_turns": 0},
        {"max_turns": 2.5},
        {"max_turns": True},
        {"context_limit_percent": 96},
        {"max_continuations": -1},
        {"nonsense": 1},
        ["max_turns"],
    ],
)
def test_validate_refuses_what_it_cannot_use(raw: object) -> None:
    with pytest.raises(LimitsError):
        validate_limits(raw)


def test_a_role_over_the_project_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUTTLEFISH_MAX_TURNS", "50")
    monkeypatch.delenv("CUTTLEFISH_ROUND_TIMEOUT", raising=False)
    layered = merge_limits({"max_turns": 30, "max_idle_rounds": 1}, {"max_turns": 10})
    assert layered == {"max_turns": 10, "max_idle_rounds": 1}
    assert max_turns_for(layered) == 10
    assert max_turns_for({}) == 50 and max_turns_for(None) == 50  # the environment fills the rest
    assert max_idle_rounds_for(layered) == 1 and max_idle_rounds_for({}) is None
    assert max_continuations_for({"max_continuations": 0}) == 0
    assert session_token_budget_for({"session_token_budget": 0}) == 0
    assert round_timeout_for({"round_timeout_minutes": 30}) == 1800.0
    assert round_timeout_for({"round_timeout_minutes": 0}) is None
    assert round_timeout_for({}) == 7200.0
    assert context_limit_for({"context_limit_percent": 60}) == 0.6
    assert context_limit_for({"context_limit_percent": 0}) is None
