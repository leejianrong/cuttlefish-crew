"""Per-role usage totals and the round-boundary hard stop they can trigger
(KAN-1712, ADR-0017).

Never a second store: usage is already journaled on the same
``DelegationCompleted``/``DelegationRefused``/``DelegationFailed`` events every
round writes (ADR-0004's "no parallel transcript"), copied there straight off
each call's own ``DelegationOutcome``. This module only sums what the journal
already holds and compares it to a configured ceiling -- the identical
"read the full record back, decide, maybe act" shape ``cuttlefish.handover``
already uses for its own token-budget checkpoint.

Scope, deliberately: a ceiling here is checked against *this run's own*
cumulative usage (the current ``task_id``/``team_id``, exactly like
``token_budget`` already is), never a lifetime or calendar-window total across
every run a project has ever started. A longer-window budget is real future
work, deferred until a persona actually needs one (ADR-0002's own "don't build
ahead of a proven need") -- this run-scoped ceiling is still real protection
against the case that actually prompted the card: one runaway round burning
far more than intended.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable

from cuttlefish.episodic.events import (
    DelegationCompleted,
    DelegationFailed,
    DelegationRefused,
    EventPayload,
)

#: Every event type a delegation's own usage can land on -- a round finalizes
#: through exactly one of these three, never more than one, so summing across
#: all of them for one role never double-counts a single round.
_USAGE_TYPES = (DelegationCompleted, DelegationRefused, DelegationFailed)


@dataclasses.dataclass(frozen=True, slots=True)
class UsageTotals:
    """One role's cumulative usage so far, as journaled -- both fields default
    to zero for a role with no usage-bearing event yet, not `None`: "nothing
    spent yet" and "unknown" are different facts, and every caller here wants
    the former."""

    tokens: int = 0
    cost_usd: float = 0.0


def cumulative_usage(payloads: Iterable[EventPayload], *, role: str | None) -> UsageTotals:
    """`role`'s total tokens/cost across every usage-bearing event in `payloads`.

    `payloads` is a full decoded episodic record (or however much of one a
    caller already has in hand) -- this never reads a store itself, mirroring
    `cuttlefish.handover.estimate_event_tokens`'s own pure, store-free shape,
    so the durable read stays exactly one call
    (`cuttlefish.tasks.journal.read_episodic_events`), not two.
    """
    tokens = 0
    cost_usd = 0.0
    for payload in payloads:
        if not isinstance(payload, _USAGE_TYPES):
            continue
        if payload.role != role:
            continue
        if payload.tokens is not None:
            tokens += payload.tokens
        if payload.cost_usd is not None:
            cost_usd += payload.cost_usd
    return UsageTotals(tokens=tokens, cost_usd=cost_usd)


def exceeded(totals: UsageTotals, *, max_tokens: int | None, max_cost_usd: float | None) -> bool:
    """Whether `totals` has crossed either configured ceiling.

    `None` means "no ceiling on this axis" (today's default posture on every
    task/team/project this project has -- an operator opts in explicitly), not
    zero -- a `max_tokens=0` would mean "stop before the very first round,"
    a real if unusual configuration, and must not read the same as "unset."
    """
    if max_tokens is not None and totals.tokens >= max_tokens:
        return True
    return max_cost_usd is not None and totals.cost_usd >= max_cost_usd
