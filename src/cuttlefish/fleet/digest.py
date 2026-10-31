"""A project's digest: the compact view a manager agent is allowed to see (CUT-150, ADR-0046).

One pure function, :func:`build_digest`, turns a project's journal and its pending requests into a
small JSON object: status, the last few events as one-line summaries, open blockers and pending
requests, and spend. It is the output of the manager's MCP digest tool and of
``GET /api/projects/{id}/digest``. The schema is in ``docs/digest.md``.

What it may carry is fixed by construction, not by filtering afterwards:

* every line is built from the *typed fields* of an allowlisted event payload (:func:`_line`), so a
  payload type nobody listed here contributes nothing, and a field nobody picked (a summary, an
  edited path, a tool call's detail, a command's output tail) cannot appear;
* the only free text is a request's title, Autopilot's goal and reason, and a pull request's URL,
  each cut to :data:`TEXT_CHARS`, flattened to one line and scrubbed of known secret values again
  (the store already redacted them at write time);
* events, requests and blockers are capped, so the size is bounded (:data:`MAX_BYTES`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Literal

from cuttlefish.budget import cumulative_usage
from cuttlefish.episodic.events import (
    AutopilotUpdated,
    DelegationCompleted,
    DelegationFailed,
    DelegationRefused,
    DelegationStarted,
    EventPayload,
    HandoverWritten,
    PullRequestOpened,
    RequestRaised,
    RequestResolved,
    RoundContinued,
    SteeringQueued,
    TaskCompleted,
    TaskFailed,
    TeamResumed,
    TeamStopped,
)
from cuttlefish.episodic.redact import Redactor
from cuttlefish.episodic.store import EpisodicEvent
from cuttlefish.fleet.status import role_statuses, roles_in

Status = Literal["working", "waiting", "stuck", "done", "idle"]

#: Longest free text (a request title, a reason, a URL) in the digest, in characters.
TEXT_CHARS = 120
#: Default and largest number of events a digest lists.
DEFAULT_EVENTS = 10
MAX_EVENTS = 25
#: At most this many pending requests and blockers are listed; ``pending_count`` says how many.
MAX_PENDING = 25
MAX_BLOCKERS = 5
#: The serialized digest stays under this (``tests/unit/test_digest.py`` holds it to it).
MAX_BYTES = 16 * 1024


def _text(text: str, redactor: Redactor) -> str:
    one_line = " ".join(redactor.scrub(text)[0].split())
    return one_line if len(one_line) <= TEXT_CHARS else one_line[: TEXT_CHARS - 1] + "…"


def _line(payload: EventPayload, redactor: Redactor) -> str | None:
    """The one-line summary of an allowlisted payload, from its typed fields, or ``None``."""
    if isinstance(payload, DelegationStarted):
        return f"Round started on {payload.backend}"
    if isinstance(payload, DelegationCompleted):
        return f"Round finished, {len(payload.edited_paths)} files edited"
    if isinstance(payload, DelegationRefused):
        return "Round refused by the agent's permission gate"
    if isinstance(payload, DelegationFailed):
        return f"Round failed ({payload.failure_kind or 'unknown cause'})"
    if isinstance(payload, TaskCompleted):
        return "Task completed"
    if isinstance(payload, TaskFailed):
        return "Task failed"
    if isinstance(payload, RequestRaised):
        return f"Asked ({payload.kind}): {_text(payload.title, redactor)}"
    if isinstance(payload, RequestResolved):
        return f"Request {payload.resolution} by {payload.by}"
    if isinstance(payload, SteeringQueued):
        return "A steering message was sent"
    if isinstance(payload, HandoverWritten):
        return "Handover written"
    if isinstance(payload, RoundContinued):
        return f"Round continued ({payload.reason}), {payload.count} of {payload.limit}"
    if isinstance(payload, TeamStopped):
        return "Team stopped"
    if isinstance(payload, TeamResumed):
        return "Team resumed after a restart"
    if isinstance(payload, PullRequestOpened):
        return f"Pull request: {_text(payload.url, redactor)}"
    return None


def _status(
    role_states: Iterable[str], autopilot: AutopilotUpdated | None, waiting: bool
) -> Status:
    states = list(role_states)
    phase = autopilot.phase if autopilot else ""
    if phase in ("stuck", "limit") or "failed" in states:
        return "stuck"
    if waiting or phase == "awaiting_approval":
        return "waiting"
    if "working" in states or phase in ("planning", "running"):
        return "working"
    if phase == "done" or (states and all(s == "done" for s in states)):
        return "done"
    return "idle"


def build_digest(
    *,
    project_id: str,
    name: str,
    events: Sequence[EpisodicEvent],
    pending: Sequence[RequestRaised],
    last_n: int = DEFAULT_EVENTS,
    redactor: Redactor | None = None,
) -> dict[str, Any]:
    """The digest of one project. ``events`` is its journal (the last team's and the session's,
    in any order), ``pending`` the requests still waiting for an answer."""
    scrub = redactor or Redactor()
    ordered = sorted(events, key=lambda e: e.ts)
    autopilot = next(
        (e.payload for e in reversed(ordered) if isinstance(e.payload, AutopilotUpdated)), None
    )
    roles = sorted(roles_in(ordered))
    states = role_statuses(ordered, roles)
    blockers = [r for r in pending if r.kind == "blocked"]
    asks = [r for r in pending if r.kind != "blocked"]

    summaries: list[dict[str, str | None]] = []
    for event in ordered:
        line = _line(event.payload, scrub)
        if line is not None:
            role = getattr(event.payload, "role", None)
            summaries.append({"at": event.ts.isoformat(), "role": role, "text": line})
    count = max(0, min(last_n, MAX_EVENTS))

    blocker_lines = [_text(r.title, scrub) for r in blockers[:MAX_BLOCKERS]]
    if autopilot and autopilot.phase in ("stuck", "limit") and len(blocker_lines) < MAX_BLOCKERS:
        blocker_lines.append(_text(f"Autopilot {autopilot.phase}: {autopilot.reason}", scrub))

    totals = [cumulative_usage((e.payload for e in ordered), role=role) for role in roles]
    costs = [t.cost_usd for t in totals if t.cost_usd is not None]
    return {
        "project_id": project_id,
        "name": _text(name, scrub),
        "status": _status(states.values(), autopilot, bool(pending)),
        "roles": {role: states[role] for role in roles},
        "autopilot": autopilot.phase if autopilot else None,
        "goal": _text(autopilot.goal, scrub) if autopilot and autopilot.goal else None,
        "last_events": summaries[-count:] if count else [],
        "blockers": blocker_lines,
        "pending_count": len(pending),
        "pending": [
            {
                "request_id": r.request_id,
                "kind": r.kind,
                "role": r.role,
                "title": _text(r.title, scrub),
                "expires_at": r.expires_at,
            }
            for r in asks[:MAX_PENDING]
        ],
        # `None` is "not reported", never zero: kopicode and Codex report no dollar cost.
        "spend": {
            "tokens": sum(t.tokens for t in totals),
            "cost_usd": sum(costs) if costs else None,
        },
    }
