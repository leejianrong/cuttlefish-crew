"""Autopilot's rules, with no I/O (CUT-174, ADR-0044).

Autopilot keeps a builder's own long-lived session going. The agent plans first and a person
approves the plan; after that, each time the agent stops with steps left on its checklist
(:mod:`cuttlefish.plan`), the daemon sends it a short "continue", until the plan is done, a limit is
reached, or the agent is stuck. :func:`decide` is the whole policy as one pure function so each rule
is a unit test; the supervisor in ``fleet/autopilot.py`` only feeds it facts and acts on the answer.

The risk this is built around is a runaway agent: every bound here ends the nudging, never the agent
mid-step, and Pause and Stop are always available to a person.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime
from typing import Literal

Phase = Literal[
    "planning",
    "awaiting_approval",
    "running",
    "paused",
    "done",
    "stopped",
    "limit",
    "stuck",
    "limit_wait",
]

#: Phases in which nothing more will happen without a person.
ENDED: frozenset[str] = frozenset({"done", "stopped", "limit", "stuck"})

DEFAULT_HOURS = 24.0
DEFAULT_TOKENS = 5_000_000
DEFAULT_NUDGES = 200
#: Nudges in a row after which no new step was ticked: the agent is going round in circles.
STUCK_AFTER = 3

#: After a usage limit, how long past the reset the "continue" is sent, so it lands inside the new
#: window and not on its edge.
RESET_GRACE_S = 60.0
#: Times in a row an agent may stop on a usage limit and be waited for before Autopilot gives up
#: (Orca stops after five failures).
MAX_LIMIT_WAITS = 5
#: A reset further off than this is not believed: it is a misread, not a window.
MAX_WAIT_S = 8 * 86400

_EPOCH = re.compile(
    r"(?:resetsAt|resets_at|anthropic-ratelimit-unified-reset)\W{1,4}(\d{9,11})", re.IGNORECASE
)
_RETRY_AFTER = re.compile(r"retry-after\W{1,4}(\d{1,7})\b", re.IGNORECASE)
_ISO = re.compile(r"\b(\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d))")


def reset_from_failure(text: str | None, now: float) -> float | None:
    """When a usage window resets, as epoch seconds, if the words of a failed turn say so; `None`
    when they do not, which is the answer, never a guess. Reads only a reset epoch (as Claude
    Code's own rate-limit fields name it), a `retry-after` in seconds, or an ISO 8601 time, and
    only one that is ahead of `now` and within `MAX_WAIT_S`."""
    if not text:
        return None
    found: list[float] = []
    for match in _EPOCH.finditer(text):
        found.append(float(match.group(1)))
    for match in _RETRY_AFTER.finditer(text):
        found.append(now + float(match.group(1)))
    for match in _ISO.finditer(text):
        try:
            found.append(datetime.fromisoformat(match.group(1).replace("Z", "+00:00")).timestamp())
        except ValueError:
            continue
    ahead = [t for t in found if now < t <= now + MAX_WAIT_S]
    return max(ahead) if ahead else None


NUDGE_TEXT = (
    "Continue with the next unticked step in .cuttlefish/artifacts/plan.md. "
    "Tick it ('- [x]') when it is done, and keep going until every step is."
)
APPROVAL_TEXT = (
    "The plan is approved. Work through it in order, ticking each step ('- [x]') in "
    ".cuttlefish/artifacts/plan.md as you finish it, and keep going until every step is done. "
    "Only stop to ask me if you are blocked."
)


@dataclasses.dataclass(frozen=True)
class Limits:
    hours: float = DEFAULT_HOURS
    tokens: int = DEFAULT_TOKENS
    nudges: int = DEFAULT_NUDGES

    def to_json(self) -> dict[str, float | int]:
        return {"hours": self.hours, "tokens": self.tokens, "nudges": self.nudges}


@dataclasses.dataclass(frozen=True)
class Facts:
    """What the supervisor knows when the agent has just stopped."""

    steps_total: int
    steps_done: int
    #: Steps done when the last nudge was sent; more now means the last nudge made progress.
    steps_done_before: int
    nudges: int
    nudges_since_progress: int
    elapsed_s: float
    tokens_used: int


@dataclasses.dataclass(frozen=True)
class Decision:
    action: Literal["nudge", "done", "limit", "stuck"]
    reason: str


def decide(facts: Facts, limits: Limits) -> Decision:
    """What to do about an agent that has stopped while Autopilot is running."""
    if facts.steps_total == 0:
        return Decision(
            "stuck", "The plan file has no checklist steps, so there is nothing to follow."
        )
    if facts.steps_done >= facts.steps_total:
        return Decision("done", "Every step of the plan is ticked.")
    if facts.elapsed_s >= limits.hours * 3600:
        return Decision("limit", f"The time limit of {_hours(limits.hours)} was reached.")
    if facts.tokens_used >= limits.tokens:
        return Decision("limit", f"The token limit of {limits.tokens:,} was reached.")
    if facts.nudges >= limits.nudges:
        return Decision("limit", f"The limit of {limits.nudges} nudges was reached.")
    progressed = facts.steps_done > facts.steps_done_before
    since = 0 if progressed else facts.nudges_since_progress
    if since >= STUCK_AFTER:
        return Decision(
            "stuck",
            f"The builder stopped {since} times in a row without ticking a step. "
            "Look at what it is doing, and say something to it or resume.",
        )
    return Decision("nudge", "Steps are left.")


def _hours(hours: float) -> str:
    return f"{hours:g} hour" + ("" if hours == 1 else "s")
