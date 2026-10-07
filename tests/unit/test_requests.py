"""Unit: the Needs-you broker (cuttlefish.requests, ADR-0028) -- no store, no process."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime

import pytest

from cuttlefish.episodic.events import EventPayload, RequestRaised, RequestResolved
from cuttlefish.episodic.store import EpisodicEvent
from cuttlefish.requests import (
    AlreadyResolvedError,
    InvalidAnswerError,
    RequestBroker,
    RequestContext,
    UnknownRequestError,
)


class Journal:
    def __init__(self) -> None:
        self.events: list[tuple[str, EventPayload]] = []

    def append(self, task_id: str, payload: EventPayload) -> EpisodicEvent:
        self.events.append((task_id, payload))
        return EpisodicEvent(task_id, len(self.events), 1, datetime.now(UTC), payload)

    def kinds(self) -> list[str]:
        return [type(p).__name__ for _, p in self.events]


def _raise(broker: RequestBroker, line: str = "docker compose up -d postgres", window: float = 60):  # type: ignore[no-untyped-def]
    return broker.raise_permission(
        project_id="p1",
        team_id="t1",
        role="builder",
        backend="kopicode",
        line=line,
        why="not on the command list",
        window_s=window,
    )


async def test_raise_journals_the_request_and_lists_it_pending() -> None:
    journal = Journal()
    broker = RequestBroker(journal.append)
    request = _raise(broker)
    assert journal.kinds() == ["RequestRaised"]
    assert journal.events[0][0] == "t1"
    assert request.record.suggested_rule == ["docker", "compose", "up"]
    assert request.record.answers == ["allow_once", "allow_always", "deny"]
    assert broker.pending() == [request]
    assert broker.pending("other") == []


async def test_allow_once_resolves_the_held_agent_and_journals_it() -> None:
    journal = Journal()
    broker = RequestBroker(journal.append)
    request = _raise(broker)
    held = asyncio.create_task(broker.hold(request))
    await asyncio.sleep(0)
    broker.answer(request.id, "allow_once")
    outcome = await held
    assert outcome.allows and outcome.resolution == "allowed_once" and outcome.by == "person"
    assert journal.kinds() == ["RequestRaised", "RequestResolved"]
    assert broker.pending() == []
    assert broker.grants("t1") == []


async def test_deny_resolves_denied() -> None:
    broker = RequestBroker(Journal().append)
    request = _raise(broker)
    held = asyncio.create_task(broker.hold(request))
    await asyncio.sleep(0)
    broker.answer(request.id, "deny")
    assert not (await held).allows


async def test_always_allow_records_the_rule_and_grants_it_to_the_team() -> None:
    journal = Journal()
    broker = RequestBroker(journal.append)
    request = _raise(broker)
    outcome = broker.answer(request.id, "allow_always")
    assert outcome.rule == ("docker", "compose", "up")
    assert broker.grants("t1") == [("docker", "compose", "up")]
    assert broker.grants("t2") == []
    resolved = journal.events[-1][1]
    assert isinstance(resolved, RequestResolved) and resolved.rule == ["docker", "compose", "up"]


async def test_an_edited_rule_must_be_the_start_of_the_command() -> None:
    broker = RequestBroker(Journal().append)
    request = _raise(broker)
    with pytest.raises(InvalidAnswerError, match="start of the command"):
        broker.answer(request.id, "allow_always", rule=["docker", "run"])
    assert broker.pending() == [request]  # a refused Always leaves it pending
    assert broker.answer(request.id, "allow_always", rule=["docker", "compose"]).rule == (
        "docker",
        "compose",
    )


@pytest.mark.parametrize("rule", [["docker", "compose", "up", "-d", "postgres", "x"], ["sudo"], []])
async def test_an_unusable_rule_is_refused(rule: list[str]) -> None:
    broker = RequestBroker(Journal().append)
    request = _raise(broker)
    with pytest.raises(InvalidAnswerError):
        broker.answer(request.id, "allow_always", rule=rule)
    assert broker.grants("t1") == []


async def test_a_launcher_rule_is_too_broad() -> None:
    broker = RequestBroker(Journal().append)
    request = _raise(broker, "python script.py")
    with pytest.raises(InvalidAnswerError, match="too broad"):
        broker.answer(request.id, "allow_always", rule=["python"])


async def test_a_line_with_shell_syntax_can_only_be_allowed_once() -> None:
    broker = RequestBroker(Journal().append)
    request = _raise(broker, "ls && make test")
    assert request.record.answers == ["allow_once", "deny"]
    assert request.record.suggested_rule is None
    with pytest.raises(InvalidAnswerError):
        broker.answer(request.id, "allow_always", rule=["ls"])


async def test_an_answer_not_offered_is_refused() -> None:
    broker = RequestBroker(Journal().append)
    request = _raise(broker)
    with pytest.raises(InvalidAnswerError):
        broker.answer(request.id, "maybe")


async def test_exactly_one_resolution_a_repeat_reports_the_first() -> None:
    journal = Journal()
    broker = RequestBroker(journal.append)
    request = _raise(broker)
    broker.answer(request.id, "deny")
    with pytest.raises(AlreadyResolvedError) as caught:
        broker.answer(request.id, "allow_once")
    assert caught.value.outcome.resolution == "denied"
    assert caught.value.status == 409
    assert journal.kinds().count("RequestResolved") == 1
    assert broker.resolve_system(request.id, "expired", by="timeout").resolution == "denied"


async def test_an_unknown_request_is_404() -> None:
    with pytest.raises(UnknownRequestError) as caught:
        RequestBroker(Journal().append).answer("nope", "deny")
    assert caught.value.status == 404


async def test_the_window_closing_denies_and_a_late_answer_conflicts() -> None:
    journal = Journal()
    broker = RequestBroker(journal.append)
    request = _raise(broker, window=0.01)
    outcome = await broker.hold(request)
    assert (outcome.resolution, outcome.by) == ("expired", "timeout") and not outcome.allows
    with pytest.raises(AlreadyResolvedError) as caught:
        broker.answer(request.id, "allow_once")
    assert caught.value.outcome.resolution == "expired"


async def test_cancelling_the_held_agent_resolves_cancelled() -> None:
    journal = Journal()
    broker = RequestBroker(journal.append)
    request = _raise(broker)
    held = asyncio.create_task(broker.hold(request))
    await asyncio.sleep(0)
    held.cancel()
    with pytest.raises(asyncio.CancelledError):
        await held
    resolved = journal.events[-1][1]
    assert isinstance(resolved, RequestResolved) and resolved.resolution == "cancelled"
    assert broker.pending() == []


async def test_end_team_ends_only_that_teams_requests() -> None:
    broker = RequestBroker(Journal().append)
    mine = _raise(broker)
    other = broker.raise_permission(
        project_id="p2", team_id="t2", role=None, backend=None, line="make", why="x", window_s=60
    )
    ended = broker.end_team("t1", "abandoned")
    assert [o.resolution for o in ended] == ["abandoned"]
    assert broker.pending() == [other] and mine.id not in {p.id for p in broker.pending()}


async def test_grants_can_be_reseeded_for_a_resumed_team() -> None:
    broker = RequestBroker(Journal().append)
    broker.seed_grants("t1", [["uv", "run", "pytest"]])
    assert broker.grants("t1") == [("uv", "run", "pytest")]


async def test_the_served_record_is_what_the_store_returned() -> None:
    """The cache keeps the written (redacted) event, never the raw one."""

    def redacting(task_id: str, payload: EventPayload) -> EpisodicEvent:
        assert isinstance(payload, RequestRaised)
        written = dataclasses.replace(payload, detail="echo [REDACTED]")
        return EpisodicEvent(task_id, 1, 1, datetime.now(UTC), written)

    broker = RequestBroker(redacting)
    request = _raise(broker, "echo sk-secret")
    assert request.record.detail == "echo [REDACTED]"


async def test_a_team_that_was_stopped_raises_no_further_request() -> None:
    """The operator's Stop ends the pending ones, and an agent that carries on until its round
    ends must not raise a fresh card nobody can answer in time (KAN-1896)."""
    journal = Journal()
    broker = RequestBroker(journal.append)
    asker = RequestContext(broker, "p1", "t1", 60).asker(role="builder", backend="kopicode")
    broker.end_team("t1", "cancelled")

    outcome = await asker("docker compose up", "not on the command list", window_s=60)

    assert (outcome.resolution, outcome.by) == ("cancelled", "system")
    assert journal.kinds() == [] and broker.pending() == []
    other = RequestContext(broker, "p1", "t2", 60).asker(role="builder", backend="kopicode")
    assert not broker.is_closed("t2") and other.context.team_id == "t2"


def _blocked(broker: RequestBroker, role: str = "builder"):  # type: ignore[no-untyped-def]
    return broker.raise_blocked(
        project_id="p1",
        team_id="t1",
        role=role,
        backend="kopicode",
        title="{who} is stuck on the project's environment",
        detail="No module named 'numpy'",
        why="its commands kept failing",
    )


async def test_a_blocked_request_has_no_answers_no_deadline_and_lands_next_round() -> None:
    broker = RequestBroker(Journal().append)
    pending = _blocked(broker)
    record = pending.record
    assert (record.kind, record.answers, record.expires_at, record.lands) == (
        "blocked",
        [],
        "",
        "next_round",
    )
    assert record.title == "builder is stuck on the project's environment"
    assert broker.pending("p1") == [pending]
    with pytest.raises(InvalidAnswerError):
        broker.answer(pending.id, "allow_once")


async def test_steering_a_role_supersedes_only_its_blocked_requests() -> None:
    store = Journal()
    broker = RequestBroker(store.append)
    mine, other = _blocked(broker), _blocked(broker, role="reviewer")
    asked = _raise(broker)
    assert [o.resolution for o in broker.supersede("t1", "builder")] == ["superseded"]
    assert {p.id for p in broker.pending()} == {other.id, asked.id}
    assert broker.supersede("t1", "builder") == []
    resolved = [p for _, p in store.events if isinstance(p, RequestResolved)]
    assert [(r.request_id, r.resolution, r.by) for r in resolved] == [
        (mine.id, "superseded", "person")
    ]


async def test_a_team_ending_ends_its_blocked_request() -> None:
    broker = RequestBroker(Journal().append)
    pending = _blocked(broker)
    assert [o.resolution for o in broker.end_team("t1", "abandoned")] == ["abandoned"]
    assert broker.pending() == [] and pending.id
