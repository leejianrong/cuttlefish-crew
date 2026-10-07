"""The backend-agnostic delegation outcome (ADR-0005).

Moved here from ``cuttlefish.delegate.kopicode`` once the delegation stopped
being kopicode-only: every :class:`~cuttlefish.agents.backend.AgentBackend`
normalises its own native output to this one shape, so nothing downstream —
the episodic journal, the workflow — has to know which backend actually ran
a delegation. ``cuttlefish.delegate.kopicode`` still imports these two names
from here; nothing about their shape changed in the move.
"""

from __future__ import annotations

import dataclasses
from typing import Literal


class DelegationError(Exception):
    """The delegation call couldn't be completed at all.

    Raised for a condition outside the backend's own recorded outcome — its
    binary missing, a stream with no parseable session in it at all. A
    refusal, or a session that ended without succeeding, is not this — see
    :class:`DelegationOutcome`.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """One individual tool invocation inside a single delegation round
    (KAN-1714, ADR-0019) — the per-call detail every backend's own
    ``classify_stream`` already reduces away when it boils a whole session
    down to one :class:`DelegationOutcome`. Recorded here, alongside that
    reduction, not instead of it.

    ``status`` is exactly one of:

    - ``"ok"`` — the call ran and its own tool reported success.
    - ``"denied"`` — the backend's own permission gate declined this specific
      call.
    - ``"error"`` — the call ran but its own tool reported a failure, for a
      reason other than a permission denial.
    """

    tool: str
    detail: str
    status: Literal["ok", "denied", "error"]


@dataclasses.dataclass(frozen=True, slots=True)
class ConsentDecisionRecord:
    """One live consent decision cuttlefish made for a delegation (KAN-1792, ADR-0021).

    ``detail`` is untrusted model output, already capped by the transport; the episodic
    store redacts it at write time like every other event text. ``answer`` is what was
    sent back to the backend, ``rule`` which policy rule (or failure mode) produced it.
    """

    kind: str
    detail: str
    answer: Literal["allow", "deny"]
    rule: str


@dataclasses.dataclass(frozen=True, slots=True)
class DelegationOutcome:
    """What one delegation call produced, boiled down to one verdict.

    ``kind`` is exactly one of:

    - ``"completed"`` — the backend finished cleanly: at least one edit
      landed, or it had nothing to do (a read-only/informational task).
    - ``"refused"`` — the backend's own permission gate declined the action
      it needed, and no edit landed as a result.
    - ``"failed"`` — the backend ran and recorded a session, but did not
      finish cleanly, for a reason other than a permission denial.
    """

    kind: Literal["completed", "refused", "failed"]
    summary: str
    edited_paths: list[str] = dataclasses.field(default_factory=list)
    reason: str | None = None
    # Usage this one delegation call burned (KAN-1712/ADR-0017), when its own
    # backend reports it -- `None` when the backend gave no such figure at all,
    # never a fabricated zero. `tokens` is a plain total (kopicode's own headless
    # surface reports only that, no prompt/completion split -- see
    # `cuttlefish.delegate.kopicode`'s own doc comment); `cost_usd` is `None` for
    # every kopicode call (it reports no dollar figure at all, and inventing a
    # per-model pricing table to estimate one is a maintenance burden and a
    # silent-drift risk this project isn't taking on, an honest gap not a fixable
    # oversight) and a real figure for Claude Code (`total_cost_usd`, verified
    # live against its own `stream-json` `result` event).
    tokens: int | None = None
    cost_usd: float | None = None
    # Every individual tool call this delegation made, in order (KAN-1714,
    # ADR-0019) -- empty when the backend made none (a plain informational
    # reply) or a refusal/failure happened before any call was even attempted.
    tool_calls: list[ToolCallRecord] = dataclasses.field(default_factory=list)
    # Why a ``"failed"`` outcome failed, when its backend can tell (only kopicode's
    # serve transport can today) -- one of ``cuttlefish.delegate.kopicode_serve
    # .FAILURE_KINDS``. ``None`` for every non-failure, and for a failure whose
    # backend gave nothing finer than ``reason``.
    failure_kind: str | None = None
    # The backend's own record of this session (kopicode's ``session.start`` ``record``: a
    # directory under the project's ``.kopicode/sessions/``), where its full output lives
    # (ADR-0029). ``None`` for a backend with no such thing.
    record: str | None = None
    # Every live consent decision this delegation's session triggered, in order
    # (KAN-1792, ADR-0021) -- empty for every transport with no live consent
    # (`run --print`, the other backends).
    consent_decisions: list[ConsentDecisionRecord] = dataclasses.field(default_factory=list)
