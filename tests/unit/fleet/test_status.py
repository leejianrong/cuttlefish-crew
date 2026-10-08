"""Unit: `cuttlefish.fleet.status.role_statuses` (ADR-0009) -- a table of episodic
event sequences to derived status, independent of a live daemon.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cuttlefish.episodic.events import (
    DelegationCompleted,
    DelegationFailed,
    DelegationRefused,
    DelegationStarted,
    EventPayload,
    HandoverWritten,
    RoundContinued,
    TaskCompleted,
    TaskFailed,
    TaskSubmitted,
    TeamStopped,
)
from cuttlefish.episodic.store import EpisodicEvent
from cuttlefish.fleet.status import role_statuses, roles_in


def _events(*payloads: EventPayload) -> list[EpisodicEvent]:
    return [
        EpisodicEvent(task_id="t", seq=i, schema_version=1, ts=datetime.now(UTC), payload=p)
        for i, p in enumerate(payloads, start=1)
    ]


def test_a_role_with_no_events_at_all_is_queued() -> None:
    assert role_statuses(_events(), ["builder"]) == {"builder": "queued"}


def test_task_submitted_only_is_queued() -> None:
    events = _events(TaskSubmitted(text="do it", role="builder"))
    assert role_statuses(events, ["builder"]) == {"builder": "queued"}


def test_delegation_started_with_no_terminal_event_is_working() -> None:
    events = _events(
        TaskSubmitted(text="do it", role="builder"),
        DelegationStarted(task_text="do it", root="/tmp", role="builder"),
    )
    assert role_statuses(events, ["builder"]) == {"builder": "working"}


def test_delegation_refused_is_blocked() -> None:
    events = _events(
        DelegationStarted(task_text="do it", root="/tmp", role="builder"),
        DelegationRefused(reason="policy denied", role="builder"),
    )
    assert role_statuses(events, ["builder"]) == {"builder": "blocked"}


def test_a_bare_delegation_failed_with_no_task_failed_yet_is_blocked_not_failed() -> None:
    """KAN-1711, found live: a require_approval-gated round that just failed is
    genuinely still open (awaiting a decision) until `TaskFailed` actually
    lands -- the dashboard's own approval panel only renders for `"blocked"`,
    so mapping this straight to `"failed"` made it invisible for exactly the
    role it most needed to appear for."""
    events = _events(
        DelegationStarted(task_text="do it", root="/tmp", role="builder"),
        DelegationFailed(reason="boom", role="builder"),
    )
    assert role_statuses(events, ["builder"]) == {"builder": "blocked"}


def test_task_completed_is_done() -> None:
    events = _events(
        DelegationStarted(task_text="do it", root="/tmp", role="builder"),
        DelegationCompleted(summary="done", role="builder"),
        TaskCompleted(result="done", role="builder"),
    )
    assert role_statuses(events, ["builder"]) == {"builder": "done"}


def test_task_failed_is_failed() -> None:
    events = _events(
        DelegationStarted(task_text="do it", root="/tmp", role="builder"),
        DelegationFailed(reason="boom", role="builder"),
        TaskFailed(error="boom", role="builder"),
    )
    assert role_statuses(events, ["builder"]) == {"builder": "failed"}


def test_a_handover_written_after_task_completed_does_not_un_terminal_it() -> None:
    """The regression `_LIFECYCLE_TYPES` exists to prevent: `run_team`'s own
    finalization order journals `TaskCompleted` *then* checkpoints a handover for
    that role -- the handover must never look like the "latest" lifecycle event."""
    events = _events(
        DelegationStarted(task_text="do it", root="/tmp", role="builder"),
        DelegationCompleted(summary="done", role="builder"),
        TaskCompleted(result="done", role="builder"),
        HandoverWritten(summary="...", covers_seq_from=1, covers_seq_to=3, role="builder"),
    )
    assert role_statuses(events, ["builder"]) == {"builder": "done"}


def test_roles_are_independent_in_one_shared_journal() -> None:
    events = _events(
        DelegationStarted(task_text="build it", root="/tmp", role="builder"),
        DelegationStarted(task_text="review it", root="/tmp", role="reviewer"),
        DelegationRefused(reason="policy denied", role="reviewer"),
    )
    assert role_statuses(events, ["builder", "reviewer"]) == {
        "builder": "working",
        "reviewer": "blocked",
    }


def test_roles_in_collects_every_role_name_a_lifecycle_event_mentions() -> None:
    events = _events(
        DelegationStarted(task_text="build it", root="/tmp", role="builder"),
        DelegationStarted(task_text="review it", root="/tmp", role="reviewer"),
    )
    assert roles_in(events) == {"builder", "reviewer"}


def test_a_stopped_team_reads_stopped_not_blocked_for_roles_that_had_not_finished() -> None:
    events = _events(
        TaskSubmitted(text="a", role="builder"),
        DelegationStarted(task_text="do it", root="/tmp", role="builder"),
        DelegationCompleted(summary="done", role="builder"),
        TaskSubmitted(text="b", role="reviewer"),
        TeamStopped(),
    )
    assert role_statuses(events, ["builder", "reviewer"]) == {
        "builder": "stopped",
        "reviewer": "stopped",
    }


def test_a_stopped_team_keeps_the_terminal_state_of_a_role_that_finished() -> None:
    events = _events(
        TaskSubmitted(text="a", role="builder"),
        TaskCompleted(result="ok", role="builder"),
        TaskSubmitted(text="b", role="reviewer"),
        DelegationStarted(task_text="do it", root="/tmp", role="reviewer"),
        TeamStopped(),
    )
    assert role_statuses(events, ["builder", "reviewer"]) == {
        "builder": "done",
        "reviewer": "stopped",
    }


def test_a_role_that_starts_again_after_a_stop_is_working() -> None:
    events = _events(
        DelegationStarted(task_text="do it", root="/tmp", role="builder"),
        TeamStopped(),
        DelegationStarted(task_text="do it", root="/tmp", role="builder"),
    )
    assert role_statuses(events, ["builder"]) == {"builder": "working"}


def test_a_round_the_team_is_continuing_is_working_not_blocked() -> None:
    """ADR-0030: a checkpoint stop with a continuation on the way is not waiting on a person."""
    failed = DelegationFailed(reason="stop=max_turns", role="builder", failure_kind="max_turns")
    events = _events(
        DelegationStarted(task_text="do it", root="/tmp", role="builder"),
        failed,
    )
    assert role_statuses(events, ["builder"]) == {"builder": "blocked"}
    events = _events(
        DelegationStarted(task_text="do it", root="/tmp", role="builder"),
        failed,
        RoundContinued(reason="max_turns", count=1, limit=20, role="builder"),
    )
    assert role_statuses(events, ["builder"]) == {"builder": "working"}
