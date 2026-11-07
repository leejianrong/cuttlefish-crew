"""A decision a builder puts to the person (CUT-184, CUT-185).

Like the plan (``plan.py``) it is a file, not a tool call, because it works the same for every
backend: the builder writes ``.cuttlefish/artifacts/decision.json`` and ends its turn, and
Autopilot, on that ``Stop``, turns it into a Needs you card of kind ``decision`` and removes the
file. The person's answer, or the recommended option once the project's wait has run out, is typed
back to the builder as text.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

DECISION_PATH = ".cuttlefish/artifacts/decision.json"

#: What a builder on Autopilot is told, after the plan instruction.
DECISION_INSTRUCTION = (
    "If you need the person to choose between ways forward, do not guess. Write "
    f'{DECISION_PATH} as JSON: {{"question": "...", "options": ["...", "..."], '
    '"recommended": "<one of the options>", "cost_of_waiting": "what stalls until they answer"}, '
    'with two to four options. Add "destructive": ["<option>"] for any option that deletes or '
    "overwrites work. If the person does not answer in time your recommended option may be taken "
    "for them, so recommend the safe one. Then stop. You will be sent the choice as a message."
)

_MAX_FILE_BYTES = 20_000

#: An option that reads like this is never chosen on the person's behalf (CUT-185). A cue, not a
#: proof: the builder's own ``destructive`` list is the other half.
_DESTRUCTIVE = re.compile(
    r"\b(delete|drop|remove|wipe|erase|destroy|purge|truncate|overwrite|reset|force|rm)\b", re.I
)


@dataclasses.dataclass(frozen=True, slots=True)
class Ask:
    question: str
    options: tuple[str, ...]
    recommended: str
    cost_of_waiting: str | None
    destructive: frozenset[str]


def is_destructive(option: str, flagged: frozenset[str] = frozenset()) -> bool:
    """Whether ``option`` may never be chosen on the person's behalf."""
    return option in flagged or _DESTRUCTIVE.search(option) is not None


def parse_ask(text: str) -> Ask | None:
    """The decision in ``text``, or ``None`` when it is not a well-formed one."""
    try:
        data: Any = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    question, options, recommended = (
        data.get("question"),
        data.get("options"),
        data.get("recommended"),
    )
    if not (
        isinstance(question, str)
        and isinstance(recommended, str)
        and isinstance(options, list)
        and all(isinstance(o, str) for o in options)
    ):
        return None
    cost, flagged = data.get("cost_of_waiting"), data.get("destructive")
    return Ask(
        question,
        tuple(options),
        recommended,
        cost if isinstance(cost, str) and cost.strip() else None,
        frozenset(o for o in flagged if isinstance(o, str))
        if isinstance(flagged, list)
        else frozenset(),
    )


def read_ask(root: str) -> Ask | None:
    """The builder's pending decision file, or ``None`` when there is none or it is malformed."""
    try:
        with (Path(root) / DECISION_PATH).open("rb") as handle:
            return parse_ask(handle.read(_MAX_FILE_BYTES).decode("utf-8", errors="replace"))
    except OSError:
        return None


def consume(root: str) -> None:
    """Remove the file once it is a card, so the same decision is not raised twice."""
    (Path(root) / DECISION_PATH).unlink(missing_ok=True)
