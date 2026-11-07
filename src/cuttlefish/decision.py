"""A decision a builder puts to the person (CUT-184).

Like the plan (``plan.py``) it is a file, not a tool call, because it works the same for every
backend: the builder writes ``.cuttlefish/artifacts/decision.json`` and ends its turn, and
Autopilot, on that ``Stop``, turns it into a Needs you card of kind ``decision`` and removes the
file. The person's answer is typed back to the builder as text.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

DECISION_PATH = ".cuttlefish/artifacts/decision.json"

#: What a builder on Autopilot is told, after the plan instruction.
DECISION_INSTRUCTION = (
    "If you need the person to choose between ways forward, do not guess. Write "
    f'{DECISION_PATH} as JSON: {{"question": "...", "options": ["...", "..."], '
    '"recommended": "<one of the options>", "cost_of_waiting": "what stalls until they answer"}, '
    "with two to four options. Then stop. You will be sent their choice as a message."
)

_MAX_FILE_BYTES = 20_000


@dataclasses.dataclass(frozen=True, slots=True)
class Ask:
    question: str
    options: tuple[str, ...]
    recommended: str
    cost_of_waiting: str | None


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
    cost = data.get("cost_of_waiting")
    return Ask(
        question,
        tuple(options),
        recommended,
        cost if isinstance(cost, str) and cost.strip() else None,
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
