"""Unit tests for `cuttlefish.budget` (KAN-1712/ADR-0017)."""

from __future__ import annotations

from cuttlefish.budget import UsageTotals, cumulative_usage, exceeded
from cuttlefish.episodic.events import (
    DelegationCompleted,
    DelegationFailed,
    DelegationRefused,
    DelegationStarted,
    TaskCompleted,
)


def test_cumulative_usage_sums_across_completed_refused_and_failed() -> None:
    payloads = [
        DelegationCompleted(summary="ok", tokens=100, cost_usd=0.01),
        DelegationRefused(reason="denied", tokens=10, cost_usd=None),
        DelegationFailed(reason="oops", tokens=50, cost_usd=0.02),
    ]
    totals = cumulative_usage(payloads, role=None)
    assert totals == UsageTotals(tokens=160, cost_usd=0.03)


def test_cumulative_usage_ignores_events_with_no_usage_data() -> None:
    payloads = [DelegationCompleted(summary="ok")]  # tokens/cost_usd both None
    assert cumulative_usage(payloads, role=None) == UsageTotals(tokens=0, cost_usd=None)


def test_cumulative_usage_ignores_non_usage_event_types() -> None:
    payloads = [
        DelegationStarted(task_text="do it", root="/scratch"),
        TaskCompleted(result="done"),
    ]
    assert cumulative_usage(payloads, role=None) == UsageTotals()


def test_cumulative_usage_filters_by_role() -> None:
    payloads = [
        DelegationCompleted(summary="ok", tokens=100, role="builder"),
        DelegationCompleted(summary="ok", tokens=999, role="reviewer"),
    ]
    assert cumulative_usage(payloads, role="builder") == UsageTotals(tokens=100)
    assert cumulative_usage(payloads, role="reviewer") == UsageTotals(tokens=999)
    assert cumulative_usage(payloads, role=None) == UsageTotals()


def test_unreported_cost_is_unknown_not_zero_and_never_trips_a_cost_ceiling() -> None:
    """KAN-1810: kopicode/Codex report no dollar figure."""
    payloads = [DelegationCompleted(summary="ok", tokens=100, cost_usd=None)]
    totals = cumulative_usage(payloads, role=None)
    assert totals == UsageTotals(tokens=100, cost_usd=None)
    assert exceeded(totals, max_tokens=None, max_cost_usd=0.0) is False


def test_exceeded_is_false_with_no_ceiling_configured() -> None:
    totals = UsageTotals(tokens=10_000_000, cost_usd=1000.0)
    assert not exceeded(totals, max_tokens=None, max_cost_usd=None)


def test_exceeded_trips_on_tokens_reaching_the_ceiling() -> None:
    assert exceeded(UsageTotals(tokens=100), max_tokens=100, max_cost_usd=None)
    assert not exceeded(UsageTotals(tokens=99), max_tokens=100, max_cost_usd=None)


def test_exceeded_trips_on_cost_reaching_the_ceiling() -> None:
    assert exceeded(UsageTotals(cost_usd=5.0), max_tokens=None, max_cost_usd=5.0)
    assert not exceeded(UsageTotals(cost_usd=4.99), max_tokens=None, max_cost_usd=5.0)


def test_exceeded_a_zero_ceiling_trips_immediately_rather_than_reading_as_unset() -> None:
    assert exceeded(UsageTotals(tokens=0), max_tokens=0, max_cost_usd=None)
