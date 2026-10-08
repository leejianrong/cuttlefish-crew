"""Episodic event types: a tagged union, versioned from the first commit (ADR-0004).

Mirrors kopicode's own journal discipline (``internal/journal/event.go``) rather than
rediscovering it: an envelope (schema version, task id, seq, timestamp — see
``cuttlefish.episodic.store``) carries exactly one typed payload, and decoding a
payload whose type this build does not recognise preserves it verbatim
(:class:`UnknownPayload`) instead of dropping it, so an old cuttlefish can still read,
and rewrite, a journal a newer one wrote.

Every payload here is a frozen dataclass with a class-level ``EVENT_TYPE`` constant —
the wire discriminator, matching kopicode's "the payload's type IS the discriminator"
choice so the two never drift apart. :func:`decode_payload` drops any field it doesn't
recognise on a *known* type rather than raising: compatible for readers, not for
rewriters (kopicode's own event.go doc comment states the identical bound), which is a
narrower promise than :class:`UnknownPayload` makes for a whole unrecognised type.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, ClassVar, Literal, cast


@dataclasses.dataclass(frozen=True, slots=True)
class TaskSubmitted:
    """The task text an operator gave cuttlefish, as submitted (PLAN.md R0)."""

    EVENT_TYPE: ClassVar[str] = "TaskSubmitted"

    text: str
    # Which team role submitted this text (ADR-0007) -- None for a plain,
    # non-team cuttlefish run (every event before this slice, and every one
    # cuttlefish run itself still writes).
    role: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class LlmCallCompleted:
    """One of cuttlefish's own reasoning calls (S5's ``LlmProvider`` seam) succeeded."""

    EVENT_TYPE: ClassVar[str] = "LlmCallCompleted"

    model: str
    prompt: str
    response: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: Which role's handover this call wrote (a team's), so the log can say whose.
    role: str | None = None
    #: What the provider reported the call cost, in US dollars (never estimated).
    cost_usd: float | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class LlmCallFailed:
    """One of cuttlefish's own reasoning calls raised rather than returning."""

    EVENT_TYPE: ClassVar[str] = "LlmCallFailed"

    model: str
    prompt: str
    error: str


@dataclasses.dataclass(frozen=True, slots=True)
class DelegationStarted:
    """A coding subtask is about to be handed to a coding-agent backend (ADR-0003, ADR-0005)."""

    EVENT_TYPE: ClassVar[str] = "DelegationStarted"

    task_text: str
    root: str
    # None before kopicode board KAN-987 landed / when no policy is configured for
    # this invocation — the call runs against kopicode's unconfigured `denyHeadless`
    # default and everything requiring permission is refused. Present once a policy
    # file is written for the call (SLICES.md V1 step 8, ADR-0002's addendum).
    policy_allow: list[list[str]] | None = None
    # None when the call ran directly on the host (V1's original, still-accepted
    # exception) rather than inside a sandbox (SLICES.md V2 step 2, KAN-1010) —
    # the backend's own name ("container", "e2b"), not an opaque flag, so the
    # journal itself says what actually ran the delegation.
    sandbox: str | None = None
    # Which AgentBackend actually ran this delegation (ADR-0005) — "kopicode" by
    # default so a V1/V2 event written before the backend became pluggable still
    # decodes to the only backend that existed then, not an empty/unknown value.
    backend: str = "kopicode"
    # Which secrets scope this delegation could read from (ADR-0006) — "default"
    # so an event written before slice B decodes to the one scope every task
    # implicitly ran under then, not an empty/unknown value.
    project: str = "default"
    # The secret *names* declared for this task (`cuttlefish run --secret NAME`),
    # never their values — a resolved value never crosses a satay task boundary,
    # let alone gets journaled here (ADR-0006).
    secret_names: list[str] = dataclasses.field(default_factory=list)
    # Which team role this delegation belongs to (ADR-0007) -- None outside a team.
    role: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class DelegationCompleted:
    """kopicode finished the delegated subtask and its edits landed."""

    EVENT_TYPE: ClassVar[str] = "DelegationCompleted"

    summary: str
    # A list, not a tuple: JSON has no tuple type, and a decoded event's field must
    # hold exactly what a freshly constructed one does, so the two round-trip
    # identically (dataclasses.asdict re-serialises whatever container is actually
    # there).
    edited_paths: list[str] = dataclasses.field(default_factory=list)
    role: str | None = None
    # This delegation's own usage (KAN-1712/ADR-0017), copied verbatim off the
    # `DelegationOutcome` that produced this event -- `None` for every event this
    # build wrote before that slice shipped, decoding to "no usage data", never a
    # fabricated zero. See `cuttlefish.agents.outcome.DelegationOutcome`'s own
    # doc comment for what each backend actually reports.
    tokens: int | None = None
    cost_usd: float | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class DelegationRefused:
    """kopicode refused the delegated action — its own permission gate said no.

    Distinct from :class:`DelegationFailed`: a refusal is kopicode's policy (or, before
    KAN-987, its unconditional headless default) declining to act, not an error in the
    call itself (docs/QUESTIONS.md Q16).
    """

    EVENT_TYPE: ClassVar[str] = "DelegationRefused"

    reason: str
    role: str | None = None
    # See `DelegationCompleted`'s identical fields (KAN-1712/ADR-0017) -- a
    # refused round still burned real usage getting there.
    tokens: int | None = None
    cost_usd: float | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class DelegationFailed:
    """The delegation call itself failed — a non-zero exit, a malformed NDJSON
    stream, or any other error kopicode did not attribute to a permission decision.
    """

    EVENT_TYPE: ClassVar[str] = "DelegationFailed"

    reason: str
    role: str | None = None
    # See `DelegationCompleted`'s identical fields (KAN-1712/ADR-0017) -- a
    # failed round still burned real usage getting there.
    tokens: int | None = None
    cost_usd: float | None = None
    # Why it failed, when the backend can tell (`DelegationOutcome.failure_kind`), and the
    # backend's own record of the session -- where its full output lives (ADR-0029): a
    # failure points at its evidence instead of a person finding it by hand.
    failure_kind: str | None = None
    record: str | None = None
    # A redacted tail of the output behind the failure (the failing command's, for an agent
    # stuck on its environment); the store redacts it again at write time.
    detail: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class HandoverWritten:
    """A working-memory handover fired at a token-budget threshold (ADR-0004, Q15).

    ``covers_seq_from``/``covers_seq_to`` point back into the full episodic record
    (inclusive) so anything that later needs the raw window can still find it —
    the handover discards it from live context, never from the journal.
    """

    EVENT_TYPE: ClassVar[str] = "HandoverWritten"

    summary: str
    covers_seq_from: int
    covers_seq_to: int
    # Which team role this handover covers (ADR-0007) -- None outside a team, so
    # this checkpoint only ever suppresses another handover for the *same* role
    # (or the same plain single-task run).
    role: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class TaskCompleted:
    """The task reached a successful terminal state."""

    EVENT_TYPE: ClassVar[str] = "TaskCompleted"

    result: str
    role: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class TaskFailed:
    """The task reached a failed terminal state."""

    EVENT_TYPE: ClassVar[str] = "TaskFailed"

    error: str
    role: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class SteeringMessage:
    """An operator's message, redirecting a still-running task or team role (ADR-0008).

    Doubles as satay's own wire payload type for delivery, not just an episodic
    event: ``cuttlefish.steering``'s ``satay.wait_for_event``/``send_event`` calls
    derive their inbox key's type name from this class's own ``__module__`` +
    ``__qualname__`` (satay's ``event_type_name``), the exact same string
    ``cuttlefish.cli``'s HTTP client sends over the wire. One shape doing both jobs,
    not two parallel event types — ADR-0004's storage separation is about *where*
    a journal lives, not about a Python type describing an event that happens to
    flow through both systems.
    """

    EVENT_TYPE: ClassVar[str] = "SteeringMessage"

    text: str
    # Which team role this message targets (ADR-0007's own discipline) -- None for
    # a plain, non-team task.
    role: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """An operator's approve/reject-with-comment decision on a just-finished
    delegation round (KAN-1711) -- the round-boundary review gate Paperclip's own
    "agent tries to close a ticket -> routed to a human reviewer" flow also uses,
    not a live per-action pause.

    Doubles as satay's own wire payload type for delivery, the same dual-purpose
    pattern `SteeringMessage` already established (ADR-0008): `cuttlefish.steering`'s
    `satay.wait_for_event`/`send_event` calls derive their inbox key's type name
    from this class's own `__module__`/`__qualname__`. `comment` is mandatory on a
    rejection (`cuttlefish approve --reject "<comment>"` enforces this on the
    sending side) and optional on an approval -- an approval needing no
    justification is the ordinary case, matching typical code-review conventions
    (e.g. GitHub's own "Approve" vs. "Request changes," the latter requiring a body).
    """

    EVENT_TYPE: ClassVar[str] = "ApprovalDecision"

    approved: bool
    comment: str | None = None
    # Which team role this decision targets (ADR-0007's own discipline) -- None for
    # a plain, non-team task.
    role: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ToolCallRecorded:
    """One individual tool invocation inside a single delegation round
    (KAN-1714, ADR-0019) — copied verbatim off the
    :class:`~cuttlefish.agents.outcome.ToolCallRecord` list every
    ``DelegationOutcome`` now carries, journaled once per entry, in order,
    right after that round's own ``DelegationCompleted``/``DelegationRefused``/
    ``DelegationFailed``. Never a replacement for those three -- this is the
    per-call detail underneath one round's own single verdict, not a second
    account of the round itself (ADR-0004's "no parallel transcript" applies
    within one round too, not only across whole stores).
    """

    EVENT_TYPE: ClassVar[str] = "ToolCallRecorded"

    tool: str
    detail: str
    status: Literal["ok", "denied", "error"]
    # Which team role this call belongs to (ADR-0007) -- None outside a team.
    role: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ConsentDecided:
    """One live consent decision cuttlefish made for a kopicode ``serve`` session
    (KAN-1792, ADR-0021) -- journaled beside the round's :class:`ToolCallRecorded` rows,
    so the dashboard shows what was asked and why it was allowed or denied, not only
    what ran. ``detail`` is the capped request detail; the store redacts it at write time.
    """

    EVENT_TYPE: ClassVar[str] = "ConsentDecided"

    kind: str
    detail: str
    answer: Literal["allow", "deny"]
    rule: str
    # Which team role this decision belongs to (ADR-0007) -- None outside a team.
    role: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class RequestRaised:
    """Something needs a person (ADR-0028): journaled live, when it is raised, not after the
    round. State is derived by folding this with :class:`RequestResolved`; there is no
    requests table. ``detail`` is the command line or question text (the store redacts it at
    write time), ``why`` says in plain words why the agent was stopped, ``answers`` is what a
    person may reply, and ``lands`` says when an answer takes effect (``now``,
    ``end_of_turn`` or ``next_round``) so a card never implies a live prompt that is not there.
    """

    EVENT_TYPE: ClassVar[str] = "RequestRaised"

    request_id: str
    kind: Literal["permission", "question", "blocked"]
    title: str
    detail: str
    why: str
    answers: list[str]
    expires_at: str
    lands: Literal["now", "end_of_turn", "next_round"] = "now"
    suggested_rule: list[str] | None = None
    role: str | None = None
    backend: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class RequestResolved:
    """How a :class:`RequestRaised` ended -- exactly one per request (ADR-0028). ``rule`` is the
    command words an Always allow added, and ``text`` is a question's answer."""

    EVENT_TYPE: ClassVar[str] = "RequestResolved"

    request_id: str
    resolution: Literal[
        "allowed_once",
        "allowed_always",
        "denied",
        "expired",
        "cancelled",
        "abandoned",
        "superseded",
        "answered",
        "declined",
    ]
    by: Literal["person", "timeout", "system"]
    rule: list[str] | None = None
    #: What a person typed to answer a ``question`` (the store redacts it at write time).
    text: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class RoundContinued:
    """A role's round ran out of room (``reason``: ``max_turns`` or ``budget_exhausted``) with no
    sign it was stuck, so the team started the next round on its own with a fresh session and the
    latest handover, instead of ending the role as failed (ADR-0030). ``count`` is how many times
    this role has continued, of at most ``limit``."""

    EVENT_TYPE: ClassVar[str] = "RoundContinued"

    reason: str
    count: int
    limit: int
    role: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class TeamStopped:
    """The operator stopped this team and it has now ended (KAN-1896). Written straight to the
    store by the daemon, like ``TeamResumed``, because satay's cancel journals nothing of its
    own: without it a role cut off between rounds reads as ``blocked``, which means "needs a
    human". Roles that had already finished keep their own terminal state."""

    EVENT_TYPE: ClassVar[str] = "TeamStopped"

    role: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class TeamResumed:
    """A daemon restart found this run still non-terminal and re-drove it via
    satay's own resume-by-run_id primitive (ADR-0010/KAN-1703) — journaled once,
    directly (not through a satay workflow — there is no workflow context at
    `cuttlefish.fleet.daemon.FleetDaemon.resume_pending`'s own call site, the same
    "write straight to the store" posture its sibling reads already hold to)
    right before the resumed run's own next event, so an operator can actually
    see continuity working rather than just trust it (KAN-1705).

    ``resumed_from_seq`` is the highest seq this journal already held the moment
    the daemon found it — exactly the "resumed after crash at seq N" marker the
    ticket asks for, distinguishing a real gap (the old process died here) from a
    round that's merely still journaling slowly.
    """

    EVENT_TYPE: ClassVar[str] = "TeamResumed"

    resumed_from_seq: int
    # Team-wide, not per-role (ADR-0007) -- a resume re-drives the whole run's
    # own workflow, not one role's delegation in isolation.
    role: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class EnvironmentPrepareStarted:
    """cuttlefish began installing one ecosystem's dependencies before the team's first round
    (ADR-0029, V5-E3). Journaled by the daemon straight to the store, ahead of any task."""

    EVENT_TYPE: ClassVar[str] = "EnvironmentPrepareStarted"

    ecosystem: str
    commands: list[list[str]]
    reason: str
    role: str | None = None
    #: The subfolder it ran in (V5-E6b); ``.`` is the project root, and what every older event is.
    path: str = "."


@dataclasses.dataclass(frozen=True, slots=True)
class EnvironmentPrepared:
    """How that install ended. ``tail`` is the last lines of its output (redacted on write);
    ``failure`` is ``exit``, ``timeout``, ``tool_missing`` or ``cancelled`` when ``ok`` is false."""

    EVENT_TYPE: ClassVar[str] = "EnvironmentPrepared"

    ecosystem: str
    ok: bool
    exit_code: int | None
    duration_s: float
    tail: str
    failure: str | None = None
    role: str | None = None
    path: str = "."


@dataclasses.dataclass(frozen=True, slots=True)
class UnknownPayload:
    """A payload whose event type this build does not recognise, preserved verbatim.

    ``event_type`` is an instance field here, not a class constant like every other
    payload's ``EVENT_TYPE``: it carries whatever the unrecognised type actually was,
    read back off the wire. Re-encoding it (:func:`encode_payload`) writes ``data``
    back unchanged, so a build that has never heard of a future event type still
    round-trips it losslessly instead of dropping it.
    """

    event_type: str
    data: dict[str, Any]


#: The tagged union. Every payload type an episodic event can carry.
EventPayload = (
    TaskSubmitted
    | LlmCallCompleted
    | LlmCallFailed
    | DelegationStarted
    | DelegationCompleted
    | DelegationRefused
    | DelegationFailed
    | HandoverWritten
    | TaskCompleted
    | TaskFailed
    | SteeringMessage
    | ApprovalDecision
    | ToolCallRecorded
    | ConsentDecided
    | RequestRaised
    | RequestResolved
    | TeamResumed
    | RoundContinued
    | TeamStopped
    | EnvironmentPrepareStarted
    | EnvironmentPrepared
    | UnknownPayload
)

#: Every known (non-:class:`UnknownPayload`) type, keyed by its wire discriminator.
_REGISTRY: Mapping[str, type[Any]] = {
    cls.EVENT_TYPE: cls
    for cls in (
        TaskSubmitted,
        LlmCallCompleted,
        LlmCallFailed,
        DelegationStarted,
        DelegationCompleted,
        DelegationRefused,
        DelegationFailed,
        HandoverWritten,
        TaskCompleted,
        TaskFailed,
        SteeringMessage,
        ApprovalDecision,
        ToolCallRecorded,
        ConsentDecided,
        RequestRaised,
        RequestResolved,
        TeamResumed,
        RoundContinued,
        TeamStopped,
        EnvironmentPrepareStarted,
        EnvironmentPrepared,
    )
}


def encode_payload(payload: EventPayload) -> tuple[str, dict[str, Any]]:
    """The wire discriminator and field dict for `payload`, ready to serialise."""
    if isinstance(payload, UnknownPayload):
        return payload.event_type, dict(payload.data)
    return payload.EVENT_TYPE, dataclasses.asdict(payload)


def decode_payload(event_type: str, data: Mapping[str, Any]) -> EventPayload:
    """The payload `event_type`/`data` decode to.

    An `event_type` this build doesn't recognise decodes to :class:`UnknownPayload`,
    holding `data` verbatim, rather than raising. A recognised type ignores any key in
    `data` it doesn't declare a field for — see the module docstring for why that's
    the correct bound rather than a silent bug.
    """
    cls = _REGISTRY.get(event_type)
    if cls is None:
        return UnknownPayload(event_type=event_type, data=dict(data))
    names = {field.name for field in dataclasses.fields(cls)}
    return cast(
        "EventPayload",
        cls(**{key: value for key, value in data.items() if key in names}),
    )
