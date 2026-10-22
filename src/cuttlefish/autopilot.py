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
from typing import Literal

Phase = Literal[
    "planning", "awaiting_approval", "running", "paused", "done", "stopped", "limit", "stuck"
]

#: Phases in which nothing more will happen without a person.
ENDED: frozenset[str] = frozenset({"done", "stopped", "limit", "stuck"})

DEFAULT_HOURS = 24.0
DEFAULT_TOKENS = 5_000_000
DEFAULT_NUDGES = 200
#: Nudges in a row after which no new step was ticked: the agent is going round in circles.
STUCK_AFTER = 3

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
