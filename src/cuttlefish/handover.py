"""Working memory: a token-budget check and an automatic handover (ADR-0004, Q15).

Never a hand-maintained document. At a configured token-budget threshold, this
distills the episodic window since the last handover (or the task's start) into
one summary via a single bounded LLM call, and writes the summary back as its own
episodic event (``HandoverWritten``) with a pointer into the full journal — nothing
is dropped from the *record*, only from what a long-running task would otherwise
keep piling into its own live context.

Checked every round of a steered ``run_task``/``run_team`` loop, not only before it
starts and after it ends (ADR-0010/KAN-1704 — the previous two-call-only shape meant
this never actually fired *during* a long steered run, the one case it exists for).
``latest_handover_summary`` is the read side: what ``cuttlefish.steering
.compose_steered_text`` folds into a fresh round's prompt so the checkpoint this
module computes actually reaches the agent's own next invocation, rather than
staying a write-only journal entry only a human reading ``cuttlefish show`` ever
sees.
"""

from __future__ import annotations

import re

from cuttlefish.episodic.events import (
    ConsentDecided,
    DelegationCompleted,
    DelegationFailed,
    DelegationRefused,
    DelegationStarted,
    EventPayload,
    HandoverWritten,
    LlmCallCompleted,
    LlmCallFailed,
    SteeringMessage,
    TaskCompleted,
    TaskFailed,
    TaskSubmitted,
    ToolCallRecorded,
    decode_payload,
)
from cuttlefish.limits import CHECKPOINT_STOPS
from cuttlefish.tasks.journal import journal, read_episodic_events
from cuttlefish.tasks.llm import call_llm
from cuttlefish.tasks.repo import REPO_STATE_MARKER, read_repo_state

#: A placeholder default, not a tuned value — callers configure their own
#: (``cuttlefish.workflow.TaskInput.token_budget``).
DEFAULT_TOKEN_BUDGET = 8_000


def estimate_tokens(text: str) -> int:
    """A rough estimate — ``len(text) // 4`` — never an exact token count.

    cuttlefish links no tokenizer, the same posture kopicode's own
    ``engine.Size.EstimatedTokens`` takes and for the identical reason: every model
    on the roadmap has a different vocabulary, and a byte estimate written as
    though it were exact would be fabricated precision.
    """
    return max(1, len(text) // 4)


def _texts(payload: EventPayload) -> tuple[str, ...]:
    """The free-text field values of `payload` that count toward its token estimate."""
    match payload:
        case TaskSubmitted(text=text):
            return (text,)
        case LlmCallCompleted():
            # The summariser's own call is journaled for its cost, not as progress: counting its
            # prompt and response here would feed every handover back into the next window.
            return ()
        case LlmCallFailed(prompt=prompt, error=error):
            return (prompt, error)
        case DelegationStarted(task_text=task_text):
            return (task_text,)
        case DelegationCompleted(summary=summary, edited_paths=paths):
            return (summary, "edited: " + ", ".join(paths)) if paths else (summary,)
        case DelegationRefused(reason=reason):
            return (reason,)
        case DelegationFailed(failure_kind=kind) if kind in CHECKPOINT_STOPS:
            # Running out of room is a checkpoint the role carries on from, not a failure: say so,
            # or a summary files it under "Failed" and the next session reads an error.
            return (f"the round ended because it ran out of room ({kind}); the work continues",)
        case DelegationFailed(reason=reason, detail=detail):
            return (reason, detail) if detail else (reason,)
        # What the agent actually did in a round. Without these a handover knows only that a round
        # ran and how it stopped, so the next session is told the task and nothing of its progress
        # (found by the first live run: a round out of turns had written a file, and the summary
        # said "results aren't captured").
        case ToolCallRecorded(tool=tool, status=status, detail=detail):
            return (f"{tool} {status}: {detail}",)
        case ConsentDecided(answer=answer, detail=detail) if answer == "deny":
            return (f"refused: {detail}",)
        case HandoverWritten(summary=summary):
            return (summary,)
        case TaskCompleted(result=result):
            return (result,)
        case TaskFailed(error=error):
            return (error,)
        case SteeringMessage(text=text):
            return (text,)
        case _:
            return ()


def estimate_event_tokens(payload: EventPayload) -> int:
    return sum(estimate_tokens(text) for text in _texts(payload))


async def maybe_handover(
    task_id: str,
    *,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    role: str | None = None,
    force: bool = False,
    root: str | None = None,
) -> bool:
    """Summarise and checkpoint if the window since the last handover is over budget.

    Reads the full episodic record via the durable ``read_episodic_events`` task
    (never the store directly — see that task's own doc comment for why), finds
    everything after the most recent ``HandoverWritten`` (or the start, if none),
    and — only once that window's estimated size crosses `token_budget` — makes one
    bounded ``call_llm`` call to distill it, then journals the result. Returns
    whether a handover was written, so a caller (mainly a test) can assert it fired.

    ``force`` writes one whatever the window's size (but not for an empty window): a fresh
    session after a checkpoint has only what it is told, and a round that ran 100 turns can
    sit well under the budget in journal tokens (ADR-0030).

    ``root`` is the project's folder: when given, the repository's own recent commits and
    uncommitted changes (read from git, never summarised) are appended to the handover, so the
    next session starts from facts about the repository, not from a model's inference (ADR-0030).

    ``role`` (ADR-0007) narrows every step to events tagged with exactly this role
    (``None`` for a plain, non-team run — the identical filter every event written
    before slice C already satisfies, since none of them ever set a ``role``). A
    team's own roles run concurrently and share one ``task_id``; without this filter
    one role's own chatty journal could force another's window closed early, or its
    own ``HandoverWritten`` could wrongly suppress a different role's next one.
    """
    raw_events = await read_episodic_events(task_id)
    all_decoded = (
        (raw["seq"], decode_payload(raw["event_type"], raw["data"])) for raw in raw_events
    )
    decoded = [
        (seq, payload) for seq, payload in all_decoded if getattr(payload, "role", None) == role
    ]

    last_handover_seq = 0
    for seq, payload in decoded:
        if isinstance(payload, HandoverWritten):
            last_handover_seq = seq

    window = [(seq, payload) for seq, payload in decoded if seq > last_handover_seq]
    if not window:
        return False

    total_tokens = sum(estimate_event_tokens(payload) for _, payload in window)
    if total_tokens < token_budget and not force:
        return False

    previous = _previous_summary(decoded, last_handover_seq)
    summary, calls = await _write_summary(window, previous)
    for call in calls:
        await journal(
            task_id,
            LlmCallCompleted(
                model=str(call["model"]),
                prompt=_prompt_of(call),
                response=str(call["text"]),
                input_tokens=_as_int(call.get("input_tokens")),
                output_tokens=_as_int(call.get("output_tokens")),
                role=role,
            ),
        )
    if root:
        state = await read_repo_state(root)
        if state:
            summary = f"{summary}\n\n{state}"

    await journal(
        task_id,
        HandoverWritten(
            summary=summary,
            covers_seq_from=window[0][0],
            covers_seq_to=window[-1][0],
            role=role,
        ),
    )
    return True


async def latest_handover_summary(task_id: str, *, role: str | None = None) -> str | None:
    """The most recent `HandoverWritten` summary for `task_id` (filtered to `role`,
    ADR-0007 — the identical filter `maybe_handover` itself uses), or `None` if none
    has fired yet.

    What `compose_steered_text` (ADR-0010/KAN-1704) folds into a fresh round's
    prompt in place of re-stating every raw round since the task began — before
    this existed, `HandoverWritten` was written but never read back by anything,
    so the checkpoint `maybe_handover` computes never actually reached the agent's
    own next invocation.
    """
    raw_events = await read_episodic_events(task_id)
    latest: str | None = None
    for raw in raw_events:
        payload = decode_payload(raw["event_type"], raw["data"])
        if isinstance(payload, HandoverWritten) and payload.role == role:
            latest = payload.summary
    return latest


_FIRST_PERSON = re.compile(
    r"^\s*(i['\u2019]?(ll|m|ve|d)\b|i (will|am|have|can)\b|let me\b|sure\b|okay\b)", re.I
)
_MIN_SUMMARY_CHARS = 20

_INSTRUCTIONS = (
    "You are writing the status report a coding agent will be handed at the start of its next "
    "session, because it will remember nothing of this one. Write it in the third person, as a "
    'plain report, never as the agent (not "I\'ll ...", not "let me ..."). Use these '
    "headings: Done (name the files and modules), Remaining, Failed or refused, Open questions. "
    "State only what the log shows. The log lists each tool call but NOT its output, so when you "
    "cannot tell whether a test passed or a commit landed, say it is unconfirmed; never say "
    "something did not happen unless the log shows it failing. Carry forward every fact from the "
    "previous summary that is still true: do not drop finished work to save space. Keep it under "
    "400 words."
)
_STRICTER = (
    "Your last answer was empty or spoke as the agent. Answer again with only the report, in the "
    "third person, starting with the heading Done."
)


def _previous_summary(
    decoded: list[tuple[int, EventPayload]], last_handover_seq: int
) -> str | None:
    """The summary the window follows, without its repository-state block: that block was true
    when it was written and is read afresh for the new one."""
    for seq, payload in decoded:
        if seq == last_handover_seq and isinstance(payload, HandoverWritten):
            text = payload.summary.split(REPO_STATE_MARKER)[0].strip()
            return text or None
    return None


def _usable(text: str) -> bool:
    """Whether a summary is a report: not empty, not a few words, not the agent talking."""
    return len(text.strip()) >= _MIN_SUMMARY_CHARS and not _FIRST_PERSON.match(text)


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _prompt_of(call: dict[str, object]) -> str:
    return str(call.get("prompt", ""))


async def _write_summary(
    window: list[tuple[int, EventPayload]], previous: str | None
) -> tuple[str, list[dict[str, object]]]:
    """The handover text and every summariser call made for it. An unusable answer is asked for
    once more, firmly; if that fails too the previous summary is carried forward unchanged (or,
    with none, a plain count of what the window holds), because an empty handover leaves the next
    session knowing nothing (found by the first 16-round real run, where two were empty and three
    were the model narrating) and a made-up one is worse."""
    calls: list[dict[str, object]] = []
    for prompt in (
        _build_summary_prompt(window, previous),
        _build_summary_prompt(window, previous, stricter=True),
    ):
        response = await call_llm(prompt)
        calls.append({**response, "prompt": prompt})
        text = response["text"]
        summary = text if isinstance(text, str) else str(text)
        if _usable(summary):
            return summary.strip(), calls
    return _fallback(window, previous), calls


def _fallback(window: list[tuple[int, EventPayload]], previous: str | None) -> str:
    files = sorted(
        {
            path
            for _, payload in window
            if isinstance(payload, DelegationCompleted | DelegationFailed | DelegationRefused)
            for path in getattr(payload, "edited_paths", [])
        }
    )
    note = (
        "(The summariser gave no usable report for the latest rounds, so this is the previous "
        "one carried forward"
        + (f"; files changed since: {', '.join(files)}" if files else "")
        + ".)"
    )
    return f"{previous}\n\n{note}" if previous else note


def _build_summary_prompt(
    window: list[tuple[int, EventPayload]], previous: str | None = None, *, stricter: bool = False
) -> str:
    lines = [_INSTRUCTIONS, ""]
    if previous:
        lines += ["Previous summary (carry forward what is still true):", previous, ""]
    lines.append("Events since then, oldest first:")
    for seq, payload in window:
        texts = _texts(payload)
        if texts:
            lines.append(f"{seq}. {type(payload).__name__}: {' | '.join(texts)}")
    if stricter:
        lines += ["", _STRICTER]
    return "\n".join(lines)
