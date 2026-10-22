"""The plan a builder writes, and what Autopilot needs to know about it (CUT-174, ADR-0044).

The plan is a markdown checklist the builder keeps at ``.cuttlefish/artifacts/plan.md``: one
``- [ ]`` line per step, ticked ``- [x]`` as it finishes each. A file, not a tool call, because it
works the same for every backend (Claude Code's own todo tool is not in every release, and Codex and
kopicode have none), it is an artifact the dashboard already shows (ADR-0036), and the person can
edit it. Autopilot reads it to decide whether steps are left; nothing else about the agent's
reasoning is parsed.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

PLAN_PATH = ".cuttlefish/artifacts/plan.md"

#: What a builder is told when it starts on Autopilot (as an addition to its system prompt for
#: Claude Code, and on the end of the goal for a tool that has no such flag).
PLAN_INSTRUCTION = (
    "Plan first. Before you change anything, write your plan as a markdown checklist in "
    f"{PLAN_PATH}: a short heading, then one '- [ ]' line per step, in order, each small enough "
    "to finish in one go. Then stop and wait for the person to approve it. After it is approved, "
    "work through the steps in order, changing '- [ ]' to '- [x]' in that file as you finish "
    "each one, and keep going until every step is done. Ask the person only if you are blocked."
)

#: A step line: a bullet or a number, then a checkbox. ``x`` or ``X`` is done.
_STEP = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[([ xX])\]\s+(.*\S)\s*$")

MAX_STEPS = 100
_STEP_CHARS = 300
_PLAN_BYTES = 200_000


@dataclasses.dataclass(frozen=True)
class Step:
    text: str
    done: bool


@dataclasses.dataclass(frozen=True)
class Plan:
    steps: tuple[Step, ...]

    @property
    def done(self) -> int:
        return sum(1 for step in self.steps if step.done)

    @property
    def remaining(self) -> int:
        return len(self.steps) - self.done

    def to_json(self) -> list[dict[str, object]]:
        return [{"text": s.text, "done": s.done} for s in self.steps]


def parse_plan(text: str) -> Plan:
    """The checklist lines of ``text``, in order. The rest of the file is the builder's prose."""
    steps: list[Step] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        match = None if in_fence else _STEP.match(line)
        if match:
            steps.append(Step(match.group(2)[:_STEP_CHARS], match.group(1) != " "))
            if len(steps) >= MAX_STEPS:
                break
    return Plan(tuple(steps))


def read_plan(root: str) -> Plan | None:
    """The project's plan, or ``None`` when the builder has not written one."""
    path = Path(root) / PLAN_PATH
    try:
        with path.open("rb") as handle:
            text = handle.read(_PLAN_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None
    return parse_plan(text)
