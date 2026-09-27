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
