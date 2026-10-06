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
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from cuttlefish.delegate.consent import (
    ConsentDecision,
    ConsentPolicy,
    ConsentPolicyError,
    command_line,
    suggest_rule,
    validate_always_rule,
)
from cuttlefish.episodic.events import EventPayload, RequestRaised, RequestResolved
from cuttlefish.episodic.store import EpisodicEvent

Answer = Literal["allow_once", "allow_always", "deny"]
Resolution = Literal[
    "allowed_once", "allowed_always", "denied", "expired", "cancelled", "abandoned"
]

#: The cancel message a serve child's reader gives the waits it abandons when the process
#: exits, so :meth:`RequestBroker.hold` can say ``abandoned`` rather than ``cancelled``.
CHILD_EXITED = "child_exited"

#: Why the agent was stopped, in the words a card shows, by the rule that refused the command.
WHY = {
    "no_shell_allowed": "Every command waits for you in Ask first.",
    "no_matching_allow_entry": "It is not on this project's command list.",
    "not_a_plain_word_list": "It chains or quotes commands, so it cannot be matched to the list.",
}

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
        self._finished: collections.OrderedDict[str, tuple[str, Outcome]] = (
            collections.OrderedDict()
        )
        self._grants: dict[str, list[tuple[str, ...]]] = {}
        self._closed: set[str] = set()

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
        except asyncio.CancelledError as exc:
            exited = bool(exc.args) and exc.args[0] == CHILD_EXITED
            self.resolve_system(request.id, "abandoned" if exited else "cancelled", by="system")
            raise

    # -- ending -----------------------------------------------------------------------

    def answer(
        self,
        request_id: str,
        answer: str,
        *,
        rule: Sequence[str] | None = None,
        project_id: str | None = None,
    ) -> Outcome:
        """A person's answer. Raises :class:`UnknownRequestError`, :class:`AlreadyResolvedError`
        or :class:`InvalidAnswerError`; a refused Always leaves the request pending. With
        ``project_id`` the request must belong to that project (the route's own)."""
        pending = self._pending.get(request_id)
        if pending is None:
            finished = self._finished.get(request_id)
            if finished is None or project_id not in (None, finished[0]):
                raise UnknownRequestError(f"no request {request_id!r}")
            raise AlreadyResolvedError(finished[1])
        if project_id not in (None, pending.project_id):
            raise UnknownRequestError(f"no request {request_id!r}")
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
            return existing[1]
        return self._finish(pending, Outcome(request_id, resolution, by))

    def end_team(
        self, team_id: str, resolution: Literal["cancelled", "abandoned"]
    ) -> list[Outcome]:
        """End every request a team still has pending: ``cancelled`` when the operator stops
        the team (the held agent is told no and its round can end), ``abandoned`` when the
        process that was waiting is gone. A team ended this way asks nobody anything more
        (:meth:`is_closed`), so an agent that carries on after a stop cannot raise a fresh one."""
        self._closed.add(team_id)
        return [
            self.resolve_system(p.id, resolution, by="system")
            for p in list(self._pending.values())
            if p.team_id == team_id
        ]

    def is_closed(self, team_id: str) -> bool:
        """Whether the team was stopped or has ended: nothing it asks now can be answered."""
        return team_id in self._closed

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
        self._finished[pending.id] = (pending.project_id, outcome)
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


def unresolved(events: Iterable[EpisodicEvent]) -> list[RequestRaised]:
    """The requests a journal raised and never resolved (what a restart must abandon)."""
    raised: dict[str, RequestRaised] = {}
    for event in events:
        payload = event.payload
        if isinstance(payload, RequestRaised):
            raised[payload.request_id] = payload
        elif isinstance(payload, RequestResolved):
            raised.pop(payload.request_id, None)
    return list(raised.values())


@dataclasses.dataclass(frozen=True, slots=True)
class HistoryEntry:
    """A request as the journal tells it: what was raised, and how (if yet) it ended."""

    raised: RequestRaised
    raised_at: datetime
    resolved: RequestResolved | None
    resolved_at: datetime | None


def history(events: Iterable[EpisodicEvent]) -> list[HistoryEntry]:
    """Every request a journal raised, oldest first, each joined to its resolution."""
    raised: dict[str, tuple[RequestRaised, datetime]] = {}
    resolved: dict[str, tuple[RequestResolved, datetime]] = {}
    for event in events:
        payload = event.payload
        if isinstance(payload, RequestRaised):
            raised[payload.request_id] = (payload, event.ts)
        elif isinstance(payload, RequestResolved) and payload.request_id not in resolved:
            resolved[payload.request_id] = (payload, event.ts)
    return [
        HistoryEntry(r, at, *(resolved.get(rid) or (None, None))) for rid, (r, at) in raised.items()
    ]


def granted_rules(events: Iterable[EpisodicEvent]) -> list[list[str]]:
    """The "always" rules a team's journal records, to re-seed a resumed team with."""
    return [
        e.payload.rule
        for e in events
        if isinstance(e.payload, RequestResolved)
        and e.payload.resolution == "allowed_always"
        and e.payload.rule
    ]


@dataclasses.dataclass(slots=True)
class RequestContext:
    """What a running team's delegations need to ask a person: the broker and who is asking.

    Carried on ``Runtime`` (never a task argument, ADR-0006). ``role_for`` attributes a request
    to a role by the task text the team dispatched, because ``satay.gather`` runs durable calls
    in satay's own tasks and a task argument for the role would change every recorded call.
    """

    broker: RequestBroker
    project_id: str
    team_id: str
    window_s: float
    _roles: dict[str, str | None] = dataclasses.field(default_factory=dict)

    def note_role(self, role: str, text: str) -> None:
        known = self._roles.get(text, role)
        self._roles[text] = role if known == role else None  # two roles, one text: unknown

    def role_for(self, text: str) -> str | None:
        return self._roles.get(text)

    def asker(self, *, role: str | None, backend: str) -> ShellAsker:
        return ShellAsker(self, role, backend)


@dataclasses.dataclass(frozen=True, slots=True)
class ShellAsker:
    """Raises a permission request for one shell command and holds until it ends."""

    context: RequestContext
    role: str | None
    backend: str

    @property
    def window_s(self) -> float:
        return self.context.window_s

    def grants(self) -> list[tuple[str, ...]]:
        return self.context.broker.grants(self.context.team_id)

    async def __call__(self, line: str, why: str, *, window_s: float) -> Outcome:
        if self.context.broker.is_closed(self.context.team_id):
            # The operator stopped this team; do not raise a card nobody will see in time.
            return Outcome("", "cancelled", "system")
        request = self.context.broker.raise_permission(
            project_id=self.context.project_id,
            team_id=self.context.team_id,
            role=self.role,
            backend=self.backend,
            line=line,
            why=why,
            window_s=window_s,
        )
        return await self.context.broker.hold(request)


class AskingDecider:
    """A consent decider that asks a person about a denial one could override.

    ``decide`` is the same :class:`ConsentPolicy` as without asking, rebuilt per decision with
    the team's granted rules so an "Always allow" applies at once. Only an *askable* denial
    becomes a request; an allow, and every denial no answer could change, is answered as before.
    ``deadline`` outlasts the request window so the broker's own expiry is what ends the wait.
    """

    def __init__(
        self,
        allow: Sequence[Sequence[str]] | None,
        asker: ShellAsker,
        *,
        window_s: float,
        auto: bool = False,
    ) -> None:
        self._allow = [list(entry) for entry in allow or ()]
        self._asker = asker
        self._window_s = window_s
        self._auto = auto
        self.deadline = window_s + 5.0

    async def __call__(self, kind: str, detail: str) -> ConsentDecision:
        policy = ConsentPolicy([*self._allow, *self._asker.grants()], auto=self._auto)
        decision = policy.decide(kind, detail)
        line = command_line(detail)
        if decision.answer == "allow" or not decision.askable or line is None:
            return decision
        outcome = await self._asker(
            line, WHY.get(decision.rule, decision.rule), window_s=self._window_s
        )
        return ConsentDecision(
            "allow" if outcome.allows else "deny", f"asked:{outcome.resolution}", asked=True
        )
