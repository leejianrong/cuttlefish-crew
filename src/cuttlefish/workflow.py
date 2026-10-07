"""The core task loop: one ``@satay.workflow``, from the first line (ADR-0001).

Slice 1's scope is narrow on purpose (docs/PLAN.md Scope): every task is handed to
kopicode wholesale, with no planning step and no clarifying question back to the
operator (QUESTIONS.md Q18). What this workflow owns is the lifecycle around that one
delegation — journaling what was asked, what was delegated, what came back, and the
terminal state. Everything a person reads back afterward (``cuttlefish show``) is
derived from exactly these events, never a second transcript (ADR-0004).
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

import satay

from cuttlefish import runtime
from cuttlefish.agents.outcome import DelegationError
from cuttlefish.budget import cumulative_usage
from cuttlefish.budget import exceeded as budget_exceeded
from cuttlefish.delegate.policy import DEFAULT_SHELL_ALLOWLIST
from cuttlefish.episodic.events import (
    ApprovalDecision,
    ConsentDecided,
    DelegationCompleted,
    DelegationFailed,
    DelegationRefused,
    DelegationStarted,
    SteeringMessage,
    TaskCompleted,
    TaskFailed,
    TaskSubmitted,
    ToolCallRecorded,
    decode_payload,
)
from cuttlefish.handover import DEFAULT_TOKEN_BUDGET, latest_handover_summary, maybe_handover
from cuttlefish.secrets.store import DEFAULT_PROJECT
from cuttlefish.steering import DEFAULT_STEERING_GRACE_SECONDS, compose_steered_text, steering_key
from cuttlefish.tasks.delegate import delegate_to_agent_backend
from cuttlefish.tasks.journal import journal, read_episodic_events


class TaskInput(TypedDict):
    """The workflow's input.

    ``task_id`` is filled in by the caller with the *same* id it passes as
    ``satay.start(..., run_id=task_id)`` (QUESTIONS.md Q6: a task's id is the satay
    run id). A workflow body has no other durable way to learn its own run id from
    the inside, so the caller mints one id and uses it both places rather than the
    workflow trying to introspect it — see ``cuttlefish.cli`` for where it's minted.

    ``token_budget`` is optional and defaults to ``handover.DEFAULT_TOKEN_BUDGET``
    — a test lowers it to force a handover deterministically rather than growing a
    real episodic window large enough to cross a realistic one.

    ``allow`` is optional and defaults to ``policy.DEFAULT_SHELL_ALLOWLIST`` (empty,
    which ``presets.resolve_allow`` turns into the built-in dev presets, ADR-0023) —
    the operator-declared, per-task policy KAN-1011 adds (docs/SLICES.md V2 step 3), each entry one
    allowed command as an argv list, in kopicode's own declared-allowlist grammar.

    ``project``/``secret_names`` (ADR-0006) are both optional and default to
    ``secrets.store.DEFAULT_PROJECT``/an empty list — a task that declares
    neither reads no project-scoped secret at all, and every backend's own
    credential still resolves from ``os.environ`` exactly as it always has.

    ``steerable`` (ADR-0008) is optional and defaults to ``False`` — a task that
    doesn't opt in runs exactly one delegation round, byte-for-byte as it always
    has (no extra ``wait_for_event`` call, no behavioural change at all). Opting
    in polls for a queued ``SteeringMessage`` after each round's outcome and, on
    a hit, runs one more round with it folded in — see ``cuttlefish.steering``
    and ADR-0008 for why this is a round-boundary redirect, not a mid-flight one.
    ``steering_grace`` overrides ``steering.DEFAULT_STEERING_GRACE_SECONDS`` — a
    test lowers it to assert a "no one steered" finalization deterministically
    rather than waiting out a real several-second grace window.

    ``require_approval`` (KAN-1711) is optional and defaults to ``False`` — when
    set, a round that nobody steered away from does not finalize on its own:
    the workflow blocks (no timeout) for an ``ApprovalDecision``
    (``cuttlefish approve <task-id>``/``--reject "<comment>"``). Approved
    finalizes with that round's own outcome, identically to today. Rejected runs
    one more round, the rejection's own comment folded in exactly the way a
    steering message already is — the two mechanisms share one redirect path,
    see ``run_task``'s own body.

    ``max_tokens``/``max_cost_usd`` (KAN-1712) are optional and default to
    ``None`` — no ceiling, today's exact behaviour. Either, once set, is
    checked every round against this run's own cumulative usage
    (``cuttlefish.budget.cumulative_usage``, summed from every
    ``DelegationCompleted``/``DelegationRefused``/``DelegationFailed`` this
    task has journaled so far): crossing it forces the *same* round-boundary
    decision wait ``require_approval`` uses — it does not add a second wait,
    for the identical satay-level reason ``require_approval`` already replaces
    rather than composes with steering (see this workflow's own body).
    """

    task_id: str
    text: str
    root: str
    token_budget: NotRequired[int]
    allow: NotRequired[list[list[str]]]
    access: NotRequired[str]
    project: NotRequired[str]
    secret_names: NotRequired[list[str]]
    steerable: NotRequired[bool]
    steering_grace: NotRequired[float]
    require_approval: NotRequired[bool]
    max_tokens: NotRequired[int]
    max_cost_usd: NotRequired[float]


@satay.workflow
async def run_task(task_input: TaskInput) -> dict[str, Any]:
    task_id = task_input["task_id"]
    text = task_input["text"]
    root = task_input["root"]
    token_budget = task_input.get("token_budget", DEFAULT_TOKEN_BUDGET)
    allow = task_input.get("allow", DEFAULT_SHELL_ALLOWLIST)
    access = task_input.get("access")
    project = task_input.get("project", DEFAULT_PROJECT)
    secret_names = task_input.get("secret_names", [])
    steerable = task_input.get("steerable", False)
    steering_grace = task_input.get("steering_grace", DEFAULT_STEERING_GRACE_SECONDS)
    require_approval = task_input.get("require_approval", False)
    max_tokens = task_input.get("max_tokens")
    max_cost_usd = task_input.get("max_cost_usd")

    await journal(task_id, TaskSubmitted(text=text))
    await maybe_handover(task_id, token_budget=token_budget)

    # Every delegation now runs behind its own backend's declared-allowlist
    # policy gate (KAN-987, ADR-0002's addendum) -- this records what was
    # actually declared for this task (KAN-1011), which agent backend ran it
    # (ADR-0005), which sandbox backend (if any) actually ran it (KAN-1010),
    # and which secrets scope/declared names it could read from (ADR-0006,
    # names only -- see delegate_to_agent_backend's own docstring for why a
    # resolved value never reaches this far) -- not just what was asked.
    runtime_ = runtime.current()
    sandbox_provider = runtime_.sandbox_provider
    sandbox_name = sandbox_provider.BACKEND_NAME if sandbox_provider is not None else None

    # Round-boundary steering (ADR-0008): a non-steerable task runs this loop's
    # body exactly once, then falls through below -- byte-for-byte the same
    # single-delegation shape this workflow always had.
    current_text = text
    round_summaries: list[str] = []
    while True:
        await journal(
            task_id,
            DelegationStarted(
                task_text=current_text,
                root=root,
                policy_allow=allow,
                sandbox=sandbox_name,
                backend=runtime_.agent_backend,
                project=project,
                secret_names=secret_names,
            ),
        )

        extra_kwargs: dict[str, Any] = {"access": access} if access else {}
        try:
            outcome = await delegate_to_agent_backend(
                current_text,
                root,
                allow=allow,
                project=project,
                secret_names=secret_names,
                **extra_kwargs,
            )
        except DelegationError as exc:
            # A plain (non-collected) awaited task's failure re-raises the task body's
            # own exception type unchanged — satay.TaskFailedError only wraps a
            # collect-mode (map/gather return_exceptions=True) failure, which this call
            # isn't (satay's replay/engine.py _execute: `if ... or not
            # _COLLECTING.get(): raise`). So this catches DelegationError itself, not a
            # satay wrapper around it. An infra-level failure like this one is never
            # steered around (ADR-0008) -- it fails the task immediately, the same as
            # it always has.
            reason = str(exc)
            await journal(task_id, DelegationFailed(reason=reason))
            await maybe_handover(task_id, token_budget=token_budget)
            await journal(task_id, TaskFailed(error=reason))
            return {"status": "failed", "error": reason}

        if outcome.kind == "completed":
            await journal(
                task_id,
                DelegationCompleted(
                    summary=outcome.summary,
                    edited_paths=outcome.edited_paths,
                    tokens=outcome.tokens,
                    cost_usd=outcome.cost_usd,
                ),
            )
        elif outcome.kind == "refused":
            await journal(
                task_id,
                DelegationRefused(
                    reason=outcome.reason or outcome.summary,
                    tokens=outcome.tokens,
                    cost_usd=outcome.cost_usd,
                ),
            )
        else:
            await journal(
                task_id,
                DelegationFailed(
                    reason=outcome.reason or outcome.summary,
                    tokens=outcome.tokens,
                    cost_usd=outcome.cost_usd,
                    failure_kind=outcome.failure_kind,
                    record=outcome.record,
                ),
            )

        # KAN-1714/ADR-0019: every individual tool call this round made,
        # journaled right after that round's own single verdict -- never a
        # replacement for it, the per-call detail underneath one outcome.
        for call in outcome.tool_calls:
            await journal(
                task_id, ToolCallRecorded(tool=call.tool, detail=call.detail, status=call.status)
            )
        # KAN-1792/ADR-0021: the live consent decisions behind those calls.
        for decision in outcome.consent_decisions:
            await journal(
                task_id,
                ConsentDecided(
                    kind=decision.kind,
                    detail=decision.detail,
                    answer=decision.answer,
                    rule=decision.rule,
                ),
            )

        # ADR-0010/KAN-1704: checked every round, not only before the loop starts
        # and after it ends -- a long steered run is exactly the case a mid-loop
        # checkpoint exists for (ADR-0004), and it previously never fired there at
        # all. A fresh handover already covers this round's own outcome (just
        # journaled above), so `round_summaries` -- the *un*-checkpointed rounds --
        # resets rather than restating it again next round.
        if await maybe_handover(task_id, token_budget=token_budget):
            round_summaries.clear()

        # A round finalizes unless something actively redirects it -- either a
        # proactive steering message (opt-in, ADR-0008) or, if require_approval is
        # set, an explicit rejection (KAN-1711). `redirect_text` is that
        # redirect's own text, shared by both mechanisms so only one "start
        # another round" code path exists below.
        #
        # require_approval *replaces* the steering wait for this round rather than
        # composing with it -- never both in the same round. Two reasons, one
        # load-bearing: (1) design -- KAN-1711's own "replacing today's ad-hoc
        # steering-only redirect" framing means the gate *is* the round-boundary
        # decision once it's on, and a rejection's own mandatory comment already
        # carries whatever an operator would otherwise have steered with, so the
        # two channels would be redundant, not complementary. (2) a real bug this
        # finding prevents: satay's own per-event-type wait identity is a bare
        # ordinal (`event#N`, `satay/replay/engine.py`'s `durable_wait_for_event`)
        # with no type discriminator baked in -- the Nth-ever wait of type A and
        # the Nth-ever wait of type B collide on the *same* identity. Awaiting
        # `SteeringMessage` then `ApprovalDecision` in the same round (both at
        # their own first-ever ordinal) hit exactly this: the steering wait's own
        # fired timeout got misread as the approval wait's, resolving it to `None`
        # immediately -- reproduced live via the fleet daemon (which always sets
        # `steerable=True`), not a synthetic case.
        redirect_text: str | None = None

        # KAN-1712/ADR-0017: a configured token/cost ceiling this role just
        # crossed forces the identical approval wait `require_approval` uses --
        # never a second wait, the same satay-level identity-collision reason
        # `require_approval` already documents. Computed from the full journal
        # this round just wrote to, exactly like `maybe_handover`'s own
        # durable read-back.
        raw_events = await read_episodic_events(task_id)
        decoded_payloads = [decode_payload(raw["event_type"], raw["data"]) for raw in raw_events]
        usage_totals = cumulative_usage(decoded_payloads, role=None)
        budget_hit = budget_exceeded(usage_totals, max_tokens=max_tokens, max_cost_usd=max_cost_usd)

        if require_approval or budget_hit:
            # No timeout: an approval gate that could silently time out into
            # "approved" would not be a gate at all (KAN-1711's own "table
            # stakes" framing -- Paperclip's own equivalent blocks until a human
            # actually decides, not until a clock runs out).
            decision = await satay.wait_for_event(
                ApprovalDecision,
                key=steering_key(task_id, None),
                timeout=None,
            )
            assert decision is not None  # no timeout was given
            await journal(task_id, decision)
            if not decision.approved:
                redirect_text = decision.comment or "(rejected, no comment given)"
        elif steerable:
            steer_event = await satay.wait_for_event(
                SteeringMessage,
                key=steering_key(task_id, None),
                timeout=steering_grace,
            )
            if steer_event is not None:
                await journal(task_id, steer_event)
                redirect_text = steer_event.text

        if redirect_text is None:
            break

        round_summaries.append(
            outcome.summary if outcome.kind == "completed" else (outcome.reason or outcome.summary)
        )
        handover_summary = await latest_handover_summary(task_id)
        current_text = compose_steered_text(
            text, round_summaries, redirect_text, handover_summary=handover_summary
        )

    await maybe_handover(task_id, token_budget=token_budget)

    if outcome.kind == "completed":
        await journal(task_id, TaskCompleted(result=outcome.summary))
        return {
            "status": "completed",
            "result": outcome.summary,
            "edited_paths": outcome.edited_paths,
        }

    reason = outcome.reason or outcome.summary
    await journal(task_id, TaskFailed(error=reason))
    return {"status": "failed", "error": reason}
