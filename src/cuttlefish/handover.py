"""Working memory: a token-budget check and an automatic handover (ADR-0004, Q15, ADR-0042).

Never a hand-maintained document. At a configured token-budget threshold, or at a checkpoint,
this writes the episodic window since the last handover (or the task's start) back as its own
episodic event (``HandoverWritten``) with a pointer into the full journal — nothing is dropped
from the *record*, only from what a long-running task would otherwise keep piling into its own
live context. The text is the agent's own handoff when it wrote one (ADR-0042, CUT-140), else a
digest read from the journal that needs no model: cuttlefish no longer asks a model of its own to
write one.

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
            # The summariser's own call (journals written before CUT-140 hold them): its cost, not
            # progress. Counting its prompt and response would feed every handover back in.
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
    agent_document: str | None = None,
) -> bool:
    """Checkpoint if the window since the last handover is over budget.

    Reads the full episodic record via the durable ``read_episodic_events`` task
    (never the store directly — see that task's own doc comment for why), finds
    everything after the most recent ``HandoverWritten`` (or the start, if none),
    and — only once that window's estimated size crosses `token_budget` — journals a
    handover for it: the agent's own document when given, else a digest of the window
    that needs no model. Returns whether a handover was written, so a caller (mainly
    a test) can assert it fired.

    ``force`` writes one whatever the window's size (but not for an empty window): a fresh
    session after a checkpoint has only what it is told, and a round that ran 100 turns can
    sit well under the budget in journal tokens (ADR-0030).

    ``agent_document`` (ADR-0042) is a handoff the agent wrote itself from its own history when it
    was asked. It is journaled as the handover as it is, and a blank one is ignored (the digest is
    written instead).

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

    if agent_document is not None and agent_document.strip():
        summary = agent_document.strip()
    else:
        summary = _digest(decoded, _previous_summary(decoded, last_handover_seq))
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


_QUOTE_CHARS = 600
_REASON_CHARS = 300
_MOST_RECENT = 5
_MOST_RECENT_STEERS = 10
#: How much of an earlier agent-written handover a digest carries forward.
_CARRIED_CHARS = 4000
_DIGEST_MARK = "(A digest read from the journal, not a report the agent wrote.)"


def _clip(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _digest(history: list[tuple[int, EventPayload]], previous: str | None) -> str:
    """A handover that needs no model (ADR-0042, CUT-140): what the journal shows about this role's
    work so far, under the same four headings the agent is asked for. It is read over the whole
    history, not the window since the last handover, so it is complete without nesting the one
    before it and its size is bounded by the caps below, however many checkpoints there were.
    Only facts the events carry, and it says plainly that it is a digest and not a report the
    agent wrote, so the next session checks the files before redoing work.

    ``previous`` is the last handover's text. A digest is not carried (this one covers it); a
    document the agent wrote is, clipped, because it holds what the journal cannot: why."""
    files: list[str] = []
    said = ""
    failures: list[str] = []
    refused: list[str] = []
    steers: list[str] = []
    for _, payload in history:
        if isinstance(payload, DelegationCompleted | DelegationFailed | DelegationRefused):
            for path in getattr(payload, "edited_paths", []):
                if path not in files:
                    files.append(path)
        if isinstance(payload, DelegationCompleted) and payload.summary.strip():
            said = payload.summary
        elif isinstance(payload, DelegationFailed):
            if payload.failure_kind in CHECKPOINT_STOPS:
                failures.append(
                    f"a round ended because it ran out of room ({payload.failure_kind})"
                )
            else:
                failures.append(_clip(payload.reason, _REASON_CHARS))
        elif isinstance(payload, DelegationRefused):
            failures.append(_clip(f"refused: {payload.reason}", _REASON_CHARS))
        elif isinstance(payload, ConsentDecided) and payload.answer == "deny":
            refused.append(_clip(payload.detail, _REASON_CHARS))
        elif isinstance(payload, SteeringMessage):
            steers.append(_clip(payload.text, _REASON_CHARS))

    done = [
        f"Files changed: {', '.join(sorted(files))}."
        if files
        else "No file change is recorded for these rounds."
    ]
    if said:
        done.append(f'The agent\'s last words: "{_clip(said, _QUOTE_CHARS)}"')
    problems = [f"- {text}" for text in failures[-_MOST_RECENT:]]
    problems += [f"- command refused: {text}" for text in refused[-_MOST_RECENT:]]
    sections = [
        _DIGEST_MARK,
        "## Done\n" + "\n".join(done),
        "## Remaining\nNot recorded: carry on from the task, and check the files above before "
        "redoing any work.",
        "## Failed or refused\n" + ("\n".join(problems) if problems else "Nothing is recorded."),
        "## Open questions\n"
        + (
            "\n".join(f'- The person said: "{text}"' for text in steers[-_MOST_RECENT_STEERS:])
            if steers
            else "None recorded."
        ),
    ]
    if previous and not previous.startswith(_DIGEST_MARK):
        carried = previous if len(previous) <= _CARRIED_CHARS else previous[:_CARRIED_CHARS] + "…"
        sections.append(f"## Earlier handover, written by the agent\n{carried}")
    return "\n\n".join(sections)
