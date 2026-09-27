# ADR-0016: A round-boundary approval gate, delivered over the same channel as steering — and replacing it, not composing with it, whenever both are set

- Status: Accepted
- Date: 2026-09-27
- Deciders: Jian

## Context

KAN-1711 (CUT-E10, first card): verified live against Paperclip's own docs
(`project_paperclip_positioning`) — their governance is *also* an
issue-boundary gate (agent tries to close a ticket → routed to a human
reviewer → approve/reject with a mandatory comment), not a live per-command
pause. "Build the cuttlefish equivalent: a role's completed round routes to
the operator for approve/reject-with-comment before being treated as
finished, replacing today's ad-hoc steering-only redirect (ADR-0008). This
is table stakes, not differentiation — we are behind on it, not ahead."

ADR-0008 already built almost every piece this needs: a round-boundary
`wait_for_event` (never nested under `satay.gather`, ADR-0008's own
correctness finding), a wire event type doubling as an episodic event
(`SteeringMessage`), a pointer-file + HTTP-client delivery mechanism
(`cuttlefish.steering`), and a CLI/dashboard pair (`cuttlefish steer`,
`RoleSteerCard.svelte`). The actual design question was narrower than it
first looked: does approval-gating *compose* with steering, or *replace* it?

## Decision

**`ApprovalDecision` is a new episodic event/wire-payload type**
(`approved: bool`, `comment: str | None`, `role: str | None`), the identical
dual-purpose pattern `SteeringMessage` already established (ADR-0008) — one
Python type serving as both satay's inbox payload and the journaled event.
`cuttlefish.steering` gains `send_approval_decision`/`APPROVAL_EVENT_TYPE`,
reusing `steering_key` verbatim: satay matches an inbox entry by
`(event_type, key)`, so `SteeringMessage` and `ApprovalDecision` sharing one
key string per role never collide on delivery.

**`TaskInput`/`TeamInput` gain `require_approval: NotRequired[bool]`**
(default `False`, team-wide like `steerable`). When set, a round that
nobody's `SteeringMessage` redirected does not finalize on its own: the
workflow blocks — **no timeout** — for an `ApprovalDecision`
(`cuttlefish approve <task-id>`/`--reject "<comment>"`, or the dashboard's
own Approve/Reject panel). Approved finalizes with that round's own outcome;
rejected runs one more round, the rejection's mandatory comment folded in via
the identical `compose_steered_text` path a steering hit already uses.
Gating applies uniformly to every real `DelegationOutcome` — completed,
refused, *and* failed — never to a collected infra-level failure
(`satay.TaskFailedError`/`DelegationError`), the same boundary ADR-0008
already drew for steering ("never a `DelegationError`").

**`require_approval`, when set, replaces the steering wait for that round —
it does not run alongside it.** This looks like it should just be "gate the
existing redirect instead of also polling for it," and an earlier version of
this ADR tried exactly that (poll `SteeringMessage` first, then
`ApprovalDecision` if nothing redirected). **That version had a real,
live-reproduced bug**, not a hypothetical one: `cuttlefish.fleet.daemon`
makes *every* daemon-started team unconditionally `steerable=True`
(ADR-0009's own decision, unchanged) — so the very first real dashboard run
with the new "require my approval" checkbox checked hit `steerable=True` and
`require_approval=True` in the same round, for the first time ever. Read
directly out of satay's own source (`satay/replay/engine.py`,
`durable_wait_for_event`), not assumed: the durable identity for one
`wait_for_event` call is `f"event#{ordinal}"` — a **bare ordinal**, scoped
per event-type by `IdentityResolver` internally but **with the type
discarded before it reaches the final identity string**. Two `wait_for_event`
calls of *different* types, both at their own first-ever ordinal within one
workflow execution, collide on the identical identity. Awaiting
`SteeringMessage` (with a timeout) and then `ApprovalDecision` (with none) in
the same round hit this exactly: the steering wait's own *fired timeout* got
misread as belonging to the approval wait, resolving it to `None`
immediately — silently raising `AssertionError` inside the workflow, visible
only as an unretrieved task exception in the fleet daemon's own log, never
surfaced to the operator, and the team looked simply abandoned (no
`TaskFailed`, `running: false`) from the dashboard's side.

Given that, the fix is a **design simplification**, not a workaround: when
`require_approval` is on, it *is* the round-boundary decision mechanism for
that round — a rejection's own mandatory comment already carries whatever an
operator would otherwise have sent as a steering message, so the two
channels were redundant, not complementary, the moment both existed for the
same round. `elif steerable:` after `if require_approval:` means only one of
the two event types is ever awaited per round, which both matches KAN-1711's
own "replacing today's ad-hoc steering-only redirect" wording exactly and
sidesteps the identity collision by construction rather than by luck.

**A second, independent live-found bug, in `cuttlefish.fleet.status`:** a
bare `DelegationFailed` with nothing journaled after it (a round that failed
and is now — with `require_approval` — genuinely still open awaiting a
decision) mapped straight to `"failed"`, not `"blocked"`. Only `TaskFailed`
is really terminal; `_status_from`'s existing bare-`DelegationCompleted`
fallback (`"blocked"`, precedent from D1's own dashboard work) already knew
this for a successful round — `DelegationFailed`/`DelegationRefused` simply
hadn't been brought into the same rule. Since the dashboard's own
Approve/Reject panel (`RoleSteerCard.svelte`) only renders for
`"blocked"`, a role stuck at `"failed"`-that's-actually-still-open was
invisible to the exact operator who most needed to see it. Fixed by folding
all three non-terminal outcome types into one `"blocked"` fallback,
verified against a real browser run afterward (screenshot: the pixel-art
sprite renders `BLOCKED`, the approval panel appears, reject → round 2 →
approve → `TaskFailed` all land correctly in the episodic journal).

**CLI**: `cuttlefish approve <task-id> [--role NAME] [--reject "<comment>"]`
mirrors `cuttlefish steer` exactly — same pointer-file lookup, same
`SteeringDeliveryError`/`FleetError` surfacing. `--reject`'s value must be
non-empty (validated *before* the pointer-file lookup, a second live-found
ordering bug: checking reachability first made a malformed `--reject` on a
nonexistent task always report "not reachable" instead of the actual input
error). `run`/`run-team` gain `--require-approval`, and the control-API-open
condition widens from `if args.steerable` to
`if args.steerable or args.require_approval` — a `require_approval`-only run
still needs the pointer file `cuttlefish approve` reaches it through.

**Daemon/dashboard**: `FleetDaemon.start`/`ProjectStore.record_team_started`
gain `require_approval` (persisted — `last_team_require_approval`, migrated
the same way `last_team_roles_json` was, for the identical resume-fidelity
reason: a restart must rebuild the *same* `TeamInput` its last start used).
`FleetDaemon.approve` mirrors `.steer`'s own `asyncio.to_thread` discipline
exactly (same-loop-deadlock reason, ADR-0009). `POST /api/projects/{id}/approve`
mirrors `/steer`; `POST .../start`'s body gains an optional `require_approval`
bool. `ProjectDetail.svelte`'s start form gains a checkbox; `RoleSteerCard.svelte`
gains an Approve/Reject panel, shown whenever `status === "blocked"` — safe to
show unconditionally there, since sending a decision to a role that was only
ever in an ordinary steering pause is a harmless, unconsumed inbox write, not
an error.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Keep both `SteeringMessage` and `ApprovalDecision` waits composed in the same round (the original design) | The bug above — a real, sourced satay-level identity collision, reproduced live via the fleet daemon's own unconditional `steerable=True`, not a synthetic edge case. |
| Fix satay-runtime's own identity scheme instead (make `event#{ordinal}` include the event type) | Real, and arguably the more "correct" long-term fix — but a durable-identity format change is exactly the kind of thing satay's own code-version/nondeterminism-policy machinery exists to gate carefully, not a narrow, quick ask (`docs/QUESTIONS.md` Q33's own "ask satay-runtime for a scoped, verified capability" bar). Cuttlefish's own workaround (never await two types in one round) is a strictly *better* design anyway, not just a stopgap — see the Decision section. Worth raising with satay-runtime separately, not blocking this card on it. |
| A `--comment` flag for approve too, symmetric with `--reject` | Skipped — Paperclip's own shape, and ordinary code-review convention (GitHub's "Approve" needs no body, "Request changes" does), ties the mandatory comment to rejection specifically. An approve that wants to leave a note has nowhere principled to put one yet; real, deferred future work if it's ever asked for. |

## Consequences

An operator can now require sign-off before any round — completed, refused,
or failed — is treated as finished, CLI or dashboard, matching Paperclip's
own governance shape without copying its per-action-pause framing (still
round-boundary, ADR-0008's own boundary unchanged). `steerable` and
`require_approval` are no longer two independently-composable knobs; a team
with both set behaves as if only `require_approval` were — a real, documented
behavior change from the design this ADR started with, not from anything
previously shipped (this is KAN-1711's own first version). Both live-found
bugs (the satay identity collision, and the status-mapping gap) were caught
by actually running this in a real browser against a real daemon, not by
the (extensive) synthetic test suite alone — neither had a red test before
the live run surfaced it; both do now
(`test_require_approval_wins_over_steerable_in_the_same_round` in both
`test_workflow_approval.py`/`test_team_approval.py`,
`test_a_bare_delegation_failed_with_no_task_failed_yet_is_blocked_not_failed`
in `test_status.py`).
