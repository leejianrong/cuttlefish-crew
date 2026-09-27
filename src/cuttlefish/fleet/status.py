"""A role's status, derived from the episodic journal (ADR-0009).

Never satay's own live worker state -- satay's own `ReadAPI` reads go direct to its
journal, never live state (`docs/QUESTIONS.md` Q50), and this project's episodic
events already carry the `role`-tagged lifecycle a status view actually needs
(ADR-0004's "no parallel transcript," held to exactly as before: no new event type,
no second source of truth).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

from cuttlefish.episodic.events import (
    DelegationCompleted,
    DelegationFailed,
    DelegationRefused,
    DelegationStarted,
    EventPayload,
    TaskCompleted,
    TaskFailed,
    TaskSubmitted,
)
from cuttlefish.episodic.store import EpisodicEvent

RoleStatus = Literal["queued", "working", "blocked", "done", "failed"]

#: Event types that actually drive a role's own state machine -- deliberately
#: excludes `HandoverWritten`/`SteeringMessage`/`ApprovalDecision`: all three are
#: real, role-tagged, and worth showing in an event tail, but none is a lifecycle
#: transition in its own right (a decision's *effect* -- another round, or
#: finalizing -- shows up as the next `DelegationStarted`/`TaskCompleted`/
#: `TaskFailed` regardless). A `HandoverWritten` written *after* a role's own
#: `TaskCompleted` (`run_team`'s own finalization order: journal the outcome, then
#: checkpoint the handover) would otherwise look like the "latest" event and
#: wrongly un-terminal a finished role.
_LIFECYCLE_TYPES: tuple[type[EventPayload], ...] = (
    TaskSubmitted,
    DelegationStarted,
    DelegationCompleted,
    DelegationRefused,
    DelegationFailed,
    TaskCompleted,
    TaskFailed,
)


def roles_in(events: Iterable[EpisodicEvent]) -> set[str]:
    """Every role name any lifecycle event in `events` is tagged with -- used to fall
    back to "whatever this journal actually mentions" when a project declared no
    role definitions of its own (`cuttlefish.fleet.daemon.FleetDaemon.status`)."""
    names: set[str] = set()
    for event in events:
        payload = event.payload
        if isinstance(payload, _LIFECYCLE_TYPES) and payload.role is not None:  # type: ignore[union-attr]
            names.add(payload.role)  # type: ignore[union-attr]
    return names


def role_statuses(events: Iterable[EpisodicEvent], roles: Sequence[str]) -> dict[str, RoleStatus]:
    """`role name -> its latest derived status`, for every name in `roles`.

    A name with no lifecycle events at all (declared but not yet started, or not a
    role this journal ever tagged) reads as `"queued"`.
    """
    latest: dict[str, EventPayload] = {}
    for event in events:
        payload = event.payload
        if not isinstance(payload, _LIFECYCLE_TYPES):
            continue
        role = payload.role  # type: ignore[union-attr]  # every _LIFECYCLE_TYPES member declares it
        if role is None or role not in roles:
            continue
        latest[role] = payload
    return {name: _status_from(latest.get(name)) for name in roles}


def _status_from(payload: EventPayload | None) -> RoleStatus:
    if payload is None or isinstance(payload, TaskSubmitted):
        return "queued"
    if isinstance(payload, TaskCompleted):
        return "done"
    if isinstance(payload, TaskFailed):
        return "failed"
    if isinstance(payload, DelegationStarted):
        return "working"
    # DelegationCompleted / DelegationRefused / DelegationFailed: a round
    # finished (whichever way) and the workflow hasn't yet journaled
    # TaskCompleted/TaskFailed or a next round's DelegationStarted -- genuinely
    # blocked on something external, whether that's the brief steering grace
    # wait (ADR-0008) or an indefinite approval-gate wait (KAN-1711). Found
    # live (KAN-1711): a *failed* round used to map straight to `"failed"`
    # here, so the dashboard's own approval panel (which only renders for
    # `"blocked"`) never appeared for a role require_approval was genuinely
    # still holding open after a failed round -- only `TaskFailed` is really
    # terminal; a bare `DelegationFailed` is exactly as "paused, not finished"
    # as a bare `DelegationCompleted` already was.
    return "blocked"
