"""A team: several named roles delegating concurrently against one project (ADR-0007).

One satay run, one ``task_id`` — not one child workflow per role (ADR-0007's own
Context explains why: a child's run id isn't known until after it starts, and
``run_task``'s own journaling needs its ``task_id`` from the first line). Every
role's delegation is, by default, a concurrent ``satay.gather`` of the same
``delegate_to_agent_backend`` task ``cuttlefish.workflow.run_task`` already uses,
tagged with ``role`` on every event it writes so one shared journal still reads back
as N independent threads of activity, and so ``maybe_handover`` can checkpoint each
role on its own, undisturbed by the others.

Two or more kopicode-backed roles sharing one ``root`` (every role in a team
already does, ``TeamInput.root`` being singular) are the one exception: kopicode's
own per-working-tree session lock refuses a second concurrent invocation against
the same root (docs/QUESTIONS.md Q44), so ``_dispatch_round`` falls back to
one-at-a-time dispatch for exactly that case instead — real, but not concurrent for
that case, rather than a hard `DelegationFailed`. A separate git worktree/checkout
per role is the actual fix for genuine concurrent editing, deliberately deferred
until this fallback's own cost is felt (ADR-0002's "don't build ahead of a proven
need").
"""

from __future__ import annotations

import logging
from typing import Any, NotRequired, TypedDict

import satay

from cuttlefish import runtime
from cuttlefish.agents.outcome import DelegationOutcome
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
    RoundContinued,
    SteeringMessage,
    TaskCompleted,
    TaskFailed,
    TaskSubmitted,
    ToolCallRecorded,
    decode_payload,
)
from cuttlefish.handover import DEFAULT_TOKEN_BUDGET, latest_handover_summary, maybe_handover
from cuttlefish.limits import (
    CHECKPOINT_STOPS,
    max_continuations_for,
    max_continuations_from_env,
    max_idle_rounds_for,
    max_idle_rounds_from_env,
)
from cuttlefish.secrets.store import DEFAULT_PROJECT
from cuttlefish.steering import DEFAULT_STEERING_GRACE_SECONDS, compose_steered_text, steering_key
from cuttlefish.tasks.delegate import delegate_to_agent_backend, raise_no_progress_card
from cuttlefish.tasks.journal import journal, read_episodic_events


class RoleInput(TypedDict):
    """One team member: a name and its own task text.

    ``allow``/``secret_names`` default to the same "nothing declared" posture a
    plain ``cuttlefish run`` takes (no declared commands, which ``presets.resolve_allow``
    turns into the built-in dev presets, ADR-0023; no declared secrets) — a role that
    needs more declares it, and what it declares is added to the presets.
    """

    name: str
    text: str
    allow: NotRequired[list[list[str]]]
    secret_names: NotRequired[list[str]]
    backend: NotRequired[str]
    access: NotRequired[str]
    presets: NotRequired[list[str]]
    #: This role's own limits (the project's laid under the role's, ADR-0030): turns, token
    #: budget, context, time per round, continuations, rounds with no change. A key it lacks
    #: falls back to the team's setting, then the environment.
    limits: NotRequired[dict[str, int]]


class TeamInput(TypedDict):
    """``run_team``'s input. ``team_id`` is the caller's own satay run id (ADR-0001's
    "task_id is the satay run id" discipline, unchanged — a team is still exactly
    one run, just one whose journal several roles share, ADR-0007).

    ``steerable`` (ADR-0008) is optional and defaults to ``False``, applying to
    every role team-wide — the same "one flag, every role" posture ``--allow``/
    ``--secret`` already take. See ``cuttlefish.workflow.TaskInput`` for what it
    does; here it's a per-role round loop that stays outside this workflow's own
    ``satay.gather`` fan-out rather than nested inside one of its members (ADR-0008's
    own reasoning for why). ``steering_grace`` overrides
    ``steering.DEFAULT_STEERING_GRACE_SECONDS`` team-wide, the same test-only
    escape hatch ``cuttlefish.workflow.TaskInput`` has.

    ``require_approval`` (KAN-1711) is optional, defaults to ``False``, and
    applies team-wide — see ``cuttlefish.workflow.TaskInput`` for the identical
    per-role semantics (blocks, no timeout, for ``cuttlefish approve <team-id>
    --role NAME``/``--reject "<comment>"`` once a round finalizes with nothing
    steering it away).

    ``max_tokens``/``max_cost_usd`` (KAN-1712) are optional, default to
    ``None`` (no ceiling), and apply team-wide — checked independently per
    role against that role's own cumulative usage, never pooled across roles.
    See ``cuttlefish.workflow.TaskInput`` for the identical per-round
    hard-stop semantics.
    """

    team_id: str
    root: str
    roles: list[RoleInput]
    project: NotRequired[str]
    token_budget: NotRequired[int]
    steerable: NotRequired[bool]
    steering_grace: NotRequired[float]
    require_approval: NotRequired[bool]
    max_tokens: NotRequired[int]
    max_cost_usd: NotRequired[float]
    #: Rounds a role may continue on its own after a turn or token stop (ADR-0030); ``None``
    #: reads ``CUTTLEFISH_MAX_CONTINUATIONS``.
    max_continuations: NotRequired[int]
    #: Auto-continued rounds in a row with no file changed before the role is held for a person
    #: (ADR-0030); ``None`` reads ``CUTTLEFISH_MAX_IDLE_ROUNDS``, ``0`` is off.
    max_idle_rounds: NotRequired[int]


logger = logging.getLogger(__name__)

#: What a round continued on its own is told, by why its predecessor stopped.
_CARRY_ON = (
    "You are already in the repository root; do not cd elsewhere. Check the repository's "
    "current state (list and read the files, git status, the tests) against the progress above, "
    "then carry on with what remains. If the task is already done, say so and stop."
)
CONTINUE_TEXT = {
    "max_turns": (
        "your previous round stopped at its turn limit, not because the task is finished. "
        + _CARRY_ON
    ),
    "budget_exhausted": (
        "your previous round used up its token budget, not because the task is finished. "
        + _CARRY_ON
    ),
    "context_pressure": (
        "your previous round was ended because its context was nearly full, not because the "
        "task is finished. " + _CARRY_ON
    ),
    "round_timeout": (
        "your previous round was stopped for running too long, not because the task is "
        "finished. " + _CARRY_ON
    ),
}


def _checkpoint_reason(outcome: DelegationOutcome) -> str | None:
    """Why a finished round is a checkpoint rather than an ending, or ``None``: it ran out of
    room (turns, tokens, time) or a command was refused, with the agent not stuck. A refusal
    is one because the agent saw it and a person or policy may have been right to say no; the
    next round is told which command, and the no-progress guard bounds a loop of them."""
    if outcome.kind == "refused":
        return "refused"
    if outcome.kind == "failed" and outcome.failure_kind in CHECKPOINT_STOPS:
        return outcome.failure_kind
    return None


def _continue_text(outcome: DelegationOutcome, why: str) -> str:
    if why != "refused":
        return CONTINUE_TEXT[why]
    denied = [call.detail for call in outcome.tool_calls if call.status == "denied"][:3]
    which = "; ".join(denied) if denied else "a command"
    return (
        f"your previous round ended after this was refused: {which}. Do not try it again as it "
        "stands: do the work another way, with your file tools or a plain command from the "
        "list, and if you truly cannot, say what you need. " + _CARRY_ON
    )


def _needs_sequential_dispatch(active_names: list[str], backend_by_name: dict[str, str]) -> bool:
    """Whether this round's delegations must run one-at-a-time instead of via
    ``satay.gather`` (docs/QUESTIONS.md Q44).

    Every role in a team already shares one ``root`` (``TeamInput.root`` is
    singular, not per-role) -- so two or more active kopicode-backed roles in one
    round always collide on kopicode's own per-working-tree session lock, not just
    in some edge case. Any other backend, or fewer than two kopicode-backed active
    roles (roles may name different backends, KAN-1809), is unaffected.
    """
    return sum(backend_by_name[name] == "kopicode" for name in active_names) > 1


def _backend_kwargs(role: RoleInput) -> dict[str, Any]:
    """A role's backend, access and presets overrides as call kwargs -- empty when it sets
    neither, so an unchanged role's task arguments stay byte-identical (replay-safe)."""
    kwargs: dict[str, Any] = {}
    backend = role.get("backend")
    if backend:
        kwargs["agent_backend"] = backend
    access = role.get("access")
    if access:
        kwargs["access"] = access
    presets = role.get("presets")
    if presets is not None:
        kwargs["presets"] = presets
    limits = role.get("limits")
    if limits:
        kwargs["limits"] = limits
    return kwargs


async def _dispatch_round(
    active_names: list[str],
    *,
    current_text: dict[str, str],
    role_by_name: dict[str, RoleInput],
    root: str,
    project: str,
    backend_by_name: dict[str, str],
) -> list[DelegationOutcome | BaseException]:
    """Run one round's delegations, choosing concurrent (``satay.gather``) or
    sequential dispatch per :func:`_needs_sequential_dispatch` (Q44) -- the
    sequential branch mirrors ``gather(..., return_exceptions=True)``'s own
    collect-mode contract by hand, catching each role's failure rather than
    letting one role's exception stop the rest of the round.
    """
    requests = runtime.current().requests
    if requests is not None:
        for name in active_names:
            requests.note_role(name, current_text[name])
    if _needs_sequential_dispatch(active_names, backend_by_name):
        outcomes: list[DelegationOutcome | BaseException] = []
        for name in active_names:
            try:
                outcomes.append(
                    await delegate_to_agent_backend(
                        current_text[name],
                        root,
                        allow=role_by_name[name].get("allow", DEFAULT_SHELL_ALLOWLIST),
                        project=project,
                        secret_names=role_by_name[name].get("secret_names"),
                        **_backend_kwargs(role_by_name[name]),
                    )
                )
            except Exception as exc:
                # Collected, not re-raised -- mirrors gather's own return_exceptions=True
                # so one role's failure doesn't stop the rest of this round.
                outcomes.append(exc)
        return outcomes

    return await satay.gather(
        *[
            delegate_to_agent_backend(
                current_text[name],
                root,
                allow=role_by_name[name].get("allow", DEFAULT_SHELL_ALLOWLIST),
                project=project,
                secret_names=role_by_name[name].get("secret_names"),
                **_backend_kwargs(role_by_name[name]),
            )
            for name in active_names
        ],
        return_exceptions=True,
    )


def _failure_reason(outcome: BaseException) -> str:
    """A journalable reason string for a collected `gather` failure (ADR-0027).

    `delegate_to_agent_backend` raising under `return_exceptions=True` arrives here
    as `satay.TaskFailedError`, which carries the original error's type name and
    message rather than the original exception itself (satay's own collect-mode
    contract) — unwrap it so a role's `DelegationFailed` reads exactly as it would
    have outside a team.
    """
    if isinstance(outcome, satay.TaskFailedError):
        return f"{outcome.error_type}: {outcome.error_message}"
    return str(outcome)


@satay.workflow
async def run_team(team_input: TeamInput) -> dict[str, Any]:
    team_id = team_input["team_id"]
    root = team_input["root"]
    roles = team_input["roles"]
    project = team_input.get("project", DEFAULT_PROJECT)
    token_budget = team_input.get("token_budget", DEFAULT_TOKEN_BUDGET)
    steerable = team_input.get("steerable", False)
    steering_grace = team_input.get("steering_grace", DEFAULT_STEERING_GRACE_SECONDS)
    require_approval = team_input.get("require_approval", False)
    max_tokens = team_input.get("max_tokens")
    max_cost_usd = team_input.get("max_cost_usd")

    for role in roles:
        await journal(team_id, TaskSubmitted(text=role["text"], role=role["name"]))
        await maybe_handover(team_id, token_budget=token_budget, role=role["name"])

    runtime_ = runtime.current()
    sandbox_provider = runtime_.sandbox_provider
    sandbox_name = sandbox_provider.BACKEND_NAME if sandbox_provider is not None else None

    role_by_name = {role["name"]: role for role in roles}
    backend_by_name = {
        role["name"]: role.get("backend") or runtime_.agent_backend for role in roles
    }
    current_text: dict[str, str] = {role["name"]: role["text"] for role in roles}
    round_summaries: dict[str, list[str]] = {role["name"]: [] for role in roles}
    team_continuations = team_input.get("max_continuations")
    if team_continuations is None:
        team_continuations = max_continuations_from_env()
    # Per role: its own limit, else the team's, else the environment's (ADR-0030).
    max_continuations_by_role: dict[str, int] = {}
    for role in roles:
        own = max_continuations_for(role.get("limits"))
        max_continuations_by_role[role["name"]] = team_continuations if own is None else own
    continuations: dict[str, int] = {role["name"]: 0 for role in roles}
    team_idle = team_input.get("max_idle_rounds")
    if team_idle is None:
        team_idle = max_idle_rounds_from_env()
    max_idle_by_role: dict[str, int] = {}
    for role in roles:
        own_idle = max_idle_rounds_for(role.get("limits"))
        max_idle_by_role[role["name"]] = team_idle if own_idle is None else own_idle
    idle_rounds: dict[str, int] = {role["name"]: 0 for role in roles}
    final_outcome: dict[str, DelegationOutcome | BaseException] = {}

    # Round-boundary steering (ADR-0008): each round still gathers only plain
    # delegation calls, the exact proven `satay.gather` shape this workflow
    # already had -- the per-role poll for a queued message happens
    # *sequentially*, after that gather resolves, never nested inside one of its
    # members (see ADR-0008 for why nesting `wait_for_event` under `gather` is an
    # unverified composition of satay's own primitives). A non-steerable team
    # runs exactly one round for every role, byte-for-byte as this workflow
    # always has.
    active_names = [role["name"] for role in roles]
    while active_names:
        for name in active_names:
            role = role_by_name[name]
            await journal(
                team_id,
                DelegationStarted(
                    task_text=current_text[name],
                    root=root,
                    policy_allow=role.get("allow", DEFAULT_SHELL_ALLOWLIST),
                    sandbox=sandbox_name,
                    backend=backend_by_name[name],
                    project=project,
                    secret_names=role.get("secret_names", []),
                    role=name,
                ),
            )

        outcomes = await _dispatch_round(
            active_names,
            current_text=current_text,
            role_by_name=role_by_name,
            root=root,
            project=project,
            backend_by_name=backend_by_name,
        )

        next_active: list[str] = []
        for name, outcome in zip(active_names, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                # An infra-level failure (ADR-0027's collected `TaskFailedError`) is
                # never steered around, the same boundary `run_task` holds for its
                # own uncollected `DelegationError` (ADR-0008) -- finalize now.
                reason = _failure_reason(outcome)
                await journal(team_id, DelegationFailed(reason=reason, role=name))
                final_outcome[name] = outcome
                continue

            assert isinstance(outcome, DelegationOutcome)
            if outcome.kind == "completed":
                await journal(
                    team_id,
                    DelegationCompleted(
                        summary=outcome.summary,
                        edited_paths=outcome.edited_paths,
                        role=name,
                        tokens=outcome.tokens,
                        cost_usd=outcome.cost_usd,
                    ),
                )
            elif outcome.kind == "refused":
                await journal(
                    team_id,
                    DelegationRefused(
                        reason=outcome.reason or outcome.summary,
                        role=name,
                        tokens=outcome.tokens,
                        cost_usd=outcome.cost_usd,
                    ),
                )
            else:
                await journal(
                    team_id,
                    DelegationFailed(
                        reason=outcome.reason or outcome.summary,
                        role=name,
                        tokens=outcome.tokens,
                        cost_usd=outcome.cost_usd,
                        failure_kind=outcome.failure_kind,
                        record=outcome.record,
                        detail=outcome.detail,
                    ),
                )
            final_outcome[name] = outcome

            # KAN-1714/ADR-0019: see run_task's identical block -- every
            # individual tool call this role's own round made, journaled
            # right after that round's single verdict.
            for call in outcome.tool_calls:
                await journal(
                    team_id,
                    ToolCallRecorded(
                        tool=call.tool, detail=call.detail, status=call.status, role=name
                    ),
                )
            # KAN-1792/ADR-0021: the live consent decisions behind those calls.
            for decision in outcome.consent_decisions:
                await journal(
                    team_id,
                    ConsentDecided(
                        kind=decision.kind,
                        detail=decision.detail,
                        answer=decision.answer,
                        rule=decision.rule,
                        role=name,
                    ),
                )

            # ADR-0010/KAN-1704: checked every round, per role, not only at a
            # role's own start and end -- see run_task's identical fix for why.
            if await maybe_handover(team_id, token_budget=token_budget, role=name):
                round_summaries[name].clear()

            # A round finalizes unless something actively redirects it -- either a
            # proactive steering message (opt-in, ADR-0008) or, if
            # require_approval is set, an explicit rejection (KAN-1711).
            # require_approval *replaces* the steering wait for this role's round
            # rather than composing with it -- see run_task's identical block for
            # both the design reason and the real satay-level identity-collision
            # bug (bare `event#N` ordinals, no type discriminator) this avoids,
            # reproduced live via the fleet daemon (always `steerable=True`).
            redirect_text: str | None = None

            # KAN-1712/ADR-0017: see run_task's identical block -- a role that
            # just crossed its own configured token/cost ceiling forces the
            # same approval wait `require_approval` uses, never a second one.
            raw_events = await read_episodic_events(team_id)
            decoded_payloads = [
                decode_payload(raw["event_type"], raw["data"]) for raw in raw_events
            ]
            usage_totals = cumulative_usage(decoded_payloads, role=name)
            budget_hit = budget_exceeded(
                usage_totals, max_tokens=max_tokens, max_cost_usd=max_cost_usd
            )

            # ADR-0030: a round that ran out of turns or tokens and was not stuck is a
            # checkpoint. Carry on from the latest handover with a fresh session. Journaled
            # before the steering grace so the role reads "working", not "blocked", while it
            # waits; a steering message in that window still wins.
            why_continue = _checkpoint_reason(outcome)
            checkpoint = why_continue is not None
            # No progress: a round that used its room and changed no file. Several in a row is
            # an agent going round in circles, so hold the role for a person.
            idle_rounds[name] = (
                (idle_rounds[name] + 1) if checkpoint and not outcome.edited_paths else 0
            )
            max_idle_rounds = max_idle_by_role[name]
            max_continuations = max_continuations_by_role[name]
            no_progress = checkpoint and 0 < max_idle_rounds <= idle_rounds[name]
            auto_continue = (
                not require_approval
                and not budget_hit
                and checkpoint
                and not no_progress
                and continuations[name] < max_continuations
            )
            if no_progress:
                await raise_no_progress_card(
                    team_id, name, backend_by_name[name], idle_rounds[name]
                )
            if auto_continue:
                continuations[name] += 1
                await journal(
                    team_id,
                    RoundContinued(
                        reason=str(why_continue),
                        count=continuations[name],
                        limit=max_continuations,
                        role=name,
                    ),
                )
                # The next session starts with nothing but what it is told, so write the handover
                # now rather than wait for the journal to cross its budget (a 100-turn round can
                # stay under it). Failing to (no summariser key) is not a reason to stop: the
                # continuation then carries the file list, as before.
                try:
                    if await maybe_handover(
                        team_id, token_budget=token_budget, role=name, force=True
                    ):
                        round_summaries[name].clear()
                except Exception:
                    logger.warning("no handover for %s at a checkpoint", name, exc_info=True)

            if require_approval or budget_hit:
                # No timeout -- see run_task's identical wait for why.
                decision = await satay.wait_for_event(
                    ApprovalDecision,
                    key=steering_key(team_id, name),
                    timeout=None,
                )
                assert decision is not None  # no timeout was given
                await journal(team_id, decision)
                if not decision.approved:
                    redirect_text = decision.comment or "(rejected, no comment given)"
            elif steerable:
                # A role cuttlefish stopped for failing on its environment (ADR-0029) waits for
                # a person with no timeout: its Needs-you card says "fix it, then steer", and a
                # five-second grace would end the team before anyone could.
                stuck = (
                    outcome.kind == "failed" and outcome.failure_kind == "environment_stuck"
                ) or no_progress
                steer_event = await satay.wait_for_event(
                    SteeringMessage,
                    key=steering_key(team_id, name),
                    timeout=None if stuck else steering_grace,
                )
                if steer_event is not None:
                    await journal(team_id, steer_event)
                    redirect_text = steer_event.text
                    idle_rounds[name] = 0  # a person looked at it: start counting again

            heading = None
            if auto_continue and redirect_text is None:
                heading = "This round was continued automatically:"
                redirect_text = _continue_text(outcome, str(why_continue))

            if redirect_text is None:
                continue

            summary = (
                outcome.summary
                if outcome.kind == "completed"
                else (outcome.reason or outcome.summary)
            )
            if outcome.edited_paths and outcome.kind != "completed":
                summary += f"; files it changed: {', '.join(outcome.edited_paths)}"
            round_summaries[name].append(summary)
            handover_summary = await latest_handover_summary(team_id, role=name)
            current_text[name] = compose_steered_text(
                role_by_name[name]["text"],
                round_summaries[name],
                redirect_text,
                handover_summary=handover_summary,
                **({"heading": heading} if heading else {}),
            )
            del final_outcome[name]
            next_active.append(name)

        active_names = next_active

    role_results: dict[str, dict[str, Any]] = {}
    for role in roles:
        name = role["name"]
        outcome = final_outcome[name]
        if isinstance(outcome, BaseException):
            reason = _failure_reason(outcome)
            await journal(team_id, TaskFailed(error=reason, role=name))
            role_results[name] = {"status": "failed", "error": reason}
        elif outcome.kind == "completed":
            await journal(team_id, TaskCompleted(result=outcome.summary, role=name))
            role_results[name] = {
                "status": "completed",
                "result": outcome.summary,
                "edited_paths": outcome.edited_paths,
            }
        else:
            reason = outcome.reason or outcome.summary
            max_idle_rounds = max_idle_by_role[name]
            max_continuations = max_continuations_by_role[name]
            if _checkpoint_reason(outcome) and idle_rounds[name] >= max_idle_rounds > 0:
                reason += (
                    f"; it changed no file in {idle_rounds[name]} rounds in a row, so it was "
                    "not continued (the rounds-with-no-change setting, CUTTLEFISH_MAX_IDLE_ROUNDS)"
                )
            elif _checkpoint_reason(outcome) and max_continuations > 0:
                reason += (
                    f"; it had already continued {continuations[name]} of {max_continuations} "
                    "times on its own (the continuations setting, CUTTLEFISH_MAX_CONTINUATIONS)"
                )
            await journal(team_id, TaskFailed(error=reason, role=name))
            role_results[name] = {"status": "failed", "error": reason}
        await maybe_handover(team_id, token_budget=token_budget, role=name)

    return {"status": _overall_status(role_results), "roles": role_results}


def _overall_status(role_results: dict[str, dict[str, Any]]) -> str:
    statuses = {result["status"] for result in role_results.values()}
    if statuses == {"completed"}:
        return "completed"
    if statuses == {"failed"}:
        return "failed"
    return "partial"
