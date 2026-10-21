"""The handoff an agent writes when a round runs out of room (ADR-0042, CUT-140).

Codex over ``app-server`` and Claude Code over stream-json cannot be asked through a method of
their own, as kopicode can (``session.handoff``): the ask is one more prompt in the live session,
with a fixed set of headings, and the reply is read back as the agent's final message. The prompt
and the check on the reply are the same for both.
"""

from __future__ import annotations

import re

#: How long the agent has to write its handoff once asked.
HANDOFF_TIMEOUT = 240.0

#: What the agent is asked for at a checkpoint: a document under fixed headings, written from
#: its own history, with no commands run.
HANDOFF_PROMPT = (
    "Your round has ended because it ran out of room. A new session will take over with no "
    "memory of this one, so write the handoff now. Do not run any command or edit any file: "
    "answer in text only. Use exactly these four headings, in this order, each on its own line "
    "starting with '## ': Done, Remaining, Failed or refused, Open questions. Done: what you "
    "finished, naming the files. Remaining: what is left, most important first. Failed or "
    "refused: anything that failed or was declined, and why. Open questions: anything the next "
    "session must decide or check. State only what you saw; say 'unconfirmed' when you did not "
    "see a test pass or a commit land. {goal}"
)


def handoff_goal(task_text: str) -> str:
    """What the next session is for."""
    return f"The next session carries on with this work: {task_text.strip()[:600]}"


def handoff_is_usable(text: str) -> bool:
    """Whether a reply is a handoff document: it has the Done and the Remaining headings. A reply
    without them is the agent talking, and the caller falls back to the journal's digest."""
    return all(
        re.search(rf"^\s*#{{1,4}}\s*{name}\b", text, re.IGNORECASE | re.MULTILINE)
        for name in ("Done", "Remaining")
    )
