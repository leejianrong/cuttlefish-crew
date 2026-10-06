"""The broker for "Needs you" requests (ADR-0028).

A request is two journal events, :class:`~cuttlefish.episodic.events.RequestRaised` and
:class:`~cuttlefish.episodic.events.RequestResolved`; the journal is the record. This module
holds only what cannot be journaled: the futures a held agent is waiting on, a cache of the
pending records (what ``EpisodicStore.append`` returned, so already redacted -- the only form
ever served), and the rules a person granted "always" to a running team.

Everything here runs on the daemon's event loop and never blocks it. An answer resolves a
future in this process; no satay control call is involved, so the ``asyncio.to_thread`` rule
does not apply. Nothing survives a restart, and nothing needs to: a pending request cannot
outlive the child process that is waiting for it.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from cuttlefish.delegate.consent import (
    ConsentPolicyError,
    suggest_rule,
    validate_always_rule,
)
from cuttlefish.episodic.events import EventPayload, RequestRaised, RequestResolved
from cuttlefish.episodic.store import EpisodicEvent

Answer = Literal["allow_once", "allow_always", "deny"]
Resolution = Literal[
    "allowed_once", "allowed_always", "denied", "expired", "cancelled", "abandoned"
]

#: How many finished requests the broker remembers, to answer a repeat idempotently.
_REMEMBERED = 200

_ANSWER_RESOLUTION: dict[str, Resolution] = {
    "allow_once": "allowed_once",
    "allow_always": "allowed_always",
    "deny": "denied",
}


class RequestError(Exception):
    """A request cannot be answered that way. ``status`` is the HTTP code the API maps it to."""

    status = 422


class UnknownRequestError(RequestError):
    status = 404


class AlreadyResolvedError(RequestError):
    """The request already ended. ``outcome`` is how, so a repeat of the same answer can be
    reported as done and anything else as a conflict."""

    status = 409

    def __init__(self, outcome: Outcome) -> None:
        super().__init__(f"request already {outcome.resolution}")
        self.outcome = outcome


class InvalidAnswerError(RequestError):
    status = 422


@dataclasses.dataclass(frozen=True, slots=True)
class Outcome:
    """How a request ended, and what to tell the agent."""

    request_id: str
    resolution: Resolution
    by: Literal["person", "timeout", "system"]
    rule: tuple[str, ...] | None = None

    @property
    def allows(self) -> bool:
        return self.resolution in ("allowed_once", "allowed_always")


@dataclasses.dataclass(slots=True)
class PendingRequest:
    """A raised request still waiting. ``record`` is the journaled (redacted) event."""

    project_id: str
    team_id: str
    record: RequestRaised
    deadline: datetime
    _future: asyncio.Future[Outcome]
    # The command exactly as the agent asked, for checking an Always rule against; held in
    # memory only and never served (the API shows ``record``, which the store redacted).
    _line: str | None = None

    @property
    def id(self) -> str:
        return self.record.request_id


def permission_answers(line: str | None) -> list[str]:
    """What a person may answer a command request with: Always allow only for a plain word list
    that a narrow-enough rule can be proposed for."""
    if line is not None and suggest_rule(line) is not None:
        return ["allow_once", "allow_always", "deny"]
    return ["allow_once", "deny"]


class RequestBroker:
    """Raises requests, holds the agent for an answer, and applies it exactly once.

    ``append`` writes one payload to a team's journal (``EpisodicStore.append``) and returns the
    event as written; it is injected so tests need no store.
    """

    def __init__(
        self,
        append: Callable[[str, EventPayload], EpisodicEvent],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._append = append
        self._now = now
        self._pending: dict[str, PendingRequest] = {}
        self._finished: collections.OrderedDict[str, Outcome] = collections.OrderedDict()
        self._grants: dict[str, list[tuple[str, ...]]] = {}

    # -- raising ----------------------------------------------------------------------

    def raise_permission(
        self,
        *,
        project_id: str,
        team_id: str,
        role: str | None,
        backend: str | None,
        line: str,
        why: str,
        window_s: float,
    ) -> PendingRequest:
        """A shell command nothing approves yet: journal it and return it to be held."""
        suggestion = suggest_rule(line)
        who = role or "An agent"
        return self._raise(
            project_id,
            team_id,
            line=line,
            record=RequestRaised(
                request_id=uuid.uuid4().hex,
                kind="permission",
                title=f"{who} wants to run a command that isn't on the list",
                detail=line,
                why=why,
                answers=permission_answers(line),
                expires_at=(self._now() + timedelta(seconds=window_s)).isoformat(),
                suggested_rule=list(suggestion) if suggestion else None,
                role=role,
                backend=backend,
            ),
            window_s=window_s,
        )

    def _raise(
        self,
        project_id: str,
        team_id: str,
        *,
        line: str | None,
        record: RequestRaised,
        window_s: float,
    ) -> PendingRequest:
        written = self._append(team_id, record)
        stored = written.payload
        assert isinstance(stored, RequestRaised)
        pending = PendingRequest(
            project_id=project_id,
            team_id=team_id,
            record=stored,
            deadline=self._now() + timedelta(seconds=window_s),
            _future=asyncio.get_running_loop().create_future(),
            _line=line,
        )
        self._pending[pending.id] = pending
        return pending

    # -- holding ----------------------------------------------------------------------

    async def hold(self, request: PendingRequest) -> Outcome:
        """Wait for the answer. Expiry resolves ``expired`` and cancellation of the waiter
        (a stopped team) resolves ``cancelled``; either way the request ends exactly once."""
        remaining = max(0.0, (request.deadline - self._now()).total_seconds())
        try:
            return await asyncio.wait_for(asyncio.shield(request._future), remaining)
        except TimeoutError:
            return self.resolve_system(request.id, "expired", by="timeout")
        except asyncio.CancelledError:
            self.resolve_system(request.id, "cancelled", by="system")
            raise

    # -- ending -----------------------------------------------------------------------

    def answer(self, request_id: str, answer: str, *, rule: Sequence[str] | None = None) -> Outcome:
        """A person's answer. Raises :class:`UnknownRequestError`, :class:`AlreadyResolvedError` or
        :class:`InvalidAnswerError`; a refused Always leaves the request pending."""
        pending = self._pending.get(request_id)
        if pending is None:
            finished = self._finished.get(request_id)
            if finished is None:
                raise UnknownRequestError(f"no request {request_id!r}")
            raise AlreadyResolvedError(finished)
        if answer not in pending.record.answers:
            raise InvalidAnswerError(f"{answer!r} is not an answer to this request")
        chosen: tuple[str, ...] | None = None
        if answer == "allow_always":
            assert pending._line is not None  # only offered for a command
            candidate = rule if rule is not None else pending.record.suggested_rule
            try:
                chosen = validate_always_rule(pending._line, candidate or ())
            except ConsentPolicyError as exc:
                raise InvalidAnswerError(str(exc)) from exc
            self._grants.setdefault(pending.team_id, []).append(chosen)
        return self._finish(
            pending, Outcome(request_id, _ANSWER_RESOLUTION[answer], "person", chosen)
        )

    def resolve_system(
        self, request_id: str, resolution: Resolution, *, by: Literal["timeout", "system"]
    ) -> Outcome:
        """End a request without a person: ``expired``, ``cancelled`` or ``abandoned``. A
        request that already ended keeps its first outcome."""
        pending = self._pending.get(request_id)
        if pending is None:
            existing = self._finished.get(request_id)
            if existing is None:
                raise UnknownRequestError(f"no request {request_id!r}")
            return existing
        return self._finish(pending, Outcome(request_id, resolution, by))

    def abandon_team(self, team_id: str) -> list[Outcome]:
        """End every request a team still has pending (its agent process ended)."""
        return [
            self.resolve_system(p.id, "abandoned", by="system")
            for p in list(self._pending.values())
            if p.team_id == team_id
        ]

    def _finish(self, pending: PendingRequest, outcome: Outcome) -> Outcome:
        self._append(
            pending.team_id,
            RequestResolved(
                request_id=outcome.request_id,
                resolution=outcome.resolution,
                by=outcome.by,
                rule=list(outcome.rule) if outcome.rule else None,
            ),
        )
        del self._pending[pending.id]
        self._finished[pending.id] = outcome
        while len(self._finished) > _REMEMBERED:
            self._finished.popitem(last=False)
        if not pending._future.done():
            pending._future.set_result(outcome)
        return outcome

    # -- reading ----------------------------------------------------------------------

    def pending(self, project_id: str | None = None) -> list[PendingRequest]:
        """Pending requests, oldest first, for one project or the whole fleet."""
        return [p for p in self._pending.values() if project_id in (None, p.project_id)]

    def grants(self, team_id: str) -> list[tuple[str, ...]]:
        """The rules a person granted "always" to this running team, applied at once."""
        return list(self._grants.get(team_id, ()))

    def seed_grants(self, team_id: str, rules: Sequence[Sequence[str]]) -> None:
        """Restore a resumed team's grants from its own journal (never from ``Project.allow``)."""
        self._grants[team_id] = [tuple(rule) for rule in rules]
