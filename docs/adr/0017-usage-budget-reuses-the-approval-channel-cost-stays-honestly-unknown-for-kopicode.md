# ADR-0017: Per-role token/cost tracking rides on the existing episodic events and its hard stop reuses the approval channel — kopicode's own cost stays honestly `None`, never a fabricated pricing-table estimate

- Status: Accepted
- Date: 2026-09-28
- Deciders: Jian

## Context

KAN-1712 (CUT-E10): "Paperclip: per-agent monthly budgets, hard stops, org/
project/goal/provider granularity. cuttlefish has none today. Minimum bar:
track $ and tokens per role per project per provider, surfaced on the
dashboard, with a configurable hard-stop that pauses a role when it's
exceeded."

Two questions had to be answered from the real backends before any design,
not assumed:

**What does each backend actually report?** Verified live (2026-09-27,
`claude` 2.1.283, real `claude -p --output-format stream-json` run): the
`result` event carries both `usage` (`input_tokens`/`output_tokens`/
`cache_creation_input_tokens`/`cache_read_input_tokens`) and
`total_cost_usd` directly — an exact dollar figure, no estimation needed.

kopicode is a different story, read directly from its own source
(`internal/journal/payload.go`, `internal/engine/event.go`), not assumed:
`journal.ProviderResponse` does carry a full `TokenCounts{Prompt, Completion,
Total}`, but `internal/engine/event.go`'s own projection from that journal
payload to the `run --print` stream's `provider_response` line copies only
`p.Tokens.Total` into the line's `size` field — the prompt/completion split
never reaches this headless surface at all, and no kind on the stream
carries anything resembling a dollar figure, ever. cuttlefish's own
`cuttlefish.delegate.kopicode.classify_stream` never even read
`provider_response` lines before this slice — they were silently dropped
along with every other unhandled `kind`.

**Where does a round's own usage actually need to be recorded and read
back?** ADR-0004's "no parallel transcript" and ADR-0016's own precedent
(reusing `SteeringMessage`'s channel rather than inventing a second one)
both point the same direction: extend what already exists rather than add a
second store or a second wire type.

## Decision

**`DelegationOutcome` (`cuttlefish.agents.outcome`) gains `tokens: int |
None` and `cost_usd: float | None`.** `None` means "this backend reported
nothing on this axis," never a fabricated zero — `ClaudeCodeBackend`
populates both (summing all four `usage` categories into `tokens`, copying
`total_cost_usd` straight through); `KopicodeBackend` populates `tokens`
only (summed across every `provider_response` line in the session, `0` when
there genuinely were none — a real, known answer, e.g. an immediate
permission denial before any provider call) and leaves `cost_usd` always
`None`. Inventing a per-model pricing table to turn kopicode's token total
into a dollar estimate was considered and rejected: OpenRouter's own rates
drift per model and per provider slug, a stale table would silently produce
a *wrong* number rather than an honestly-missing one, and it's exactly the
kind of thing this project's own culture (ADR-0005's "honest approximation,
not full parity" language for Claude Code's own allowlist mapping) already
warns against building speculatively. The dashboard shows tokens and cost
independently for exactly this reason — a token count with no dollar figure
next to it is not a rendering bug.

**`DelegationCompleted`/`DelegationRefused`/`DelegationFailed`
(`cuttlefish.episodic.events`) each gain the identical two optional fields**,
copied straight off the `DelegationOutcome` that produced them at the exact
call sites `cuttlefish.workflow.run_task`/`cuttlefish.team.run_team` already
journal those events from — no new event type, no second store, ADR-0004's
own discipline held to exactly. A round's usage is therefore already durable
and already readable by anything that reads the journal (`cuttlefish show`,
the dashboard's event log) with zero new plumbing.

**`cuttlefish.budget`** is the one new module: `cumulative_usage(payloads,
*, role)` sums tokens/cost across every `DelegationCompleted`/
`DelegationRefused`/`DelegationFailed` tagged with `role` (mirroring
`cuttlefish.handover.estimate_event_tokens`'s own pure, store-free shape),
and `exceeded(totals, *, max_tokens, max_cost_usd)` compares against a
configured ceiling — `None` on either axis means "no ceiling," and
`0` is a real, meaningful configuration ("stop before the very first
round"), not read as unset.

**Scope, deliberately: run-scoped, not lifetime.** A ceiling is checked
against *this run's own* cumulative usage (the current `task_id`/`team_id`),
the identical scope `token_budget`'s own handover checkpoint already uses —
never a calendar-window or lifetime-across-every-run total. A project
launching ten teams over a month with `max_cost_usd=5.0` gets a fresh $5
ceiling each run, not a shared monthly pool. This is a real, named
simplification (ADR-0002's own "don't build ahead of a proven need"): a
lifetime counter needs its own persistence story (summing across every
`task_id` a project's episodic store has ever held, potentially many), and
nothing has proven that's the granularity operators actually want yet versus
"catch one runaway round" (KAN-1712's own framing) — this ceiling already
solves the latter completely.

**The hard stop reuses the exact `ApprovalDecision`/`steering_key` channel
ADR-0016 already built — it does not add a third wire type.**
`TaskInput`/`TeamInput` gain `max_tokens: NotRequired[int]`/`max_cost_usd:
NotRequired[float]` (default `None`, checked independently per role in a
team, never pooled). Every round, right after journaling that round's
outcome event, `run_task`/`run_team` read the full journal back
(`read_episodic_events`, the identical durable call `maybe_handover` already
makes) and compute `budget.cumulative_usage`/`budget.exceeded` for this
role. The existing gate condition:

```python
if require_approval:
    ...
elif steerable:
    ...
```

becomes:

```python
if require_approval or budget_hit:
    ...
elif steerable:
    ...
```

`budget_hit` forces the identical no-timeout `ApprovalDecision` wait
`require_approval` already uses, for exactly the reason ADR-0016 already
established: two `wait_for_event` calls of different types at their own
first-ever ordinal in one round collide on satay's own bare-ordinal wait
identity (`event#N`, `satay/replay/engine.py`). A configured budget being
crossed is, mechanically, one more way a round can need the operator's own
decision before finalizing — not a reason to invent a fourth code path.
Approving a budget-exceeded round finalizes it as-is (the operator saying
"yes, stop here"); rejecting starts one more round, which — since usage only
grows — will hit the identical ceiling again at its own next boundary,
requiring its own explicit decision. A ceiling therefore never lets a run
past it silently, round after round, without a human saying so each time.

**The CLI's `needs_control_api` widens the same way `require_approval`
already widened it.** `run --max-tokens`/`--max-cost-usd` (and their
`run-team` equivalents) open the control API/pointer file even with neither
`--steerable` nor `--require-approval` set — a budget-only run that hit its
own ceiling with no control API open would have no way for `cuttlefish
approve` to ever reach it, the identical gap ADR-0016 closed for
`--require-approval` alone.

**`Project` (`cuttlefish.projects.store`) gains persisted `max_tokens: int |
None`/`max_cost_usd: float | None`**, migrated the same way `allow_json` was
— reviewed once per project, not retyped per `FleetDaemon.start` call, the
identical Q53 precedent `allow` already set. `cuttlefish projects add` gains
`--max-tokens`/`--max-cost-usd`; `PATCH /api/projects/{id}/budget` mirrors
`/allow`'s own update route exactly (and, unlike `/allow`, is actually wired
into the dashboard's registration form — a small `Max tokens`/`Max cost per
role` field pair — since usage/budget display is this card's whole point,
not a follow-on).

**Dashboard**: `_project_json` gains `budget: {max_tokens, max_cost_usd}`
and `usage: {role: {tokens, cost_usd}}`, both derived the identical way
`status` already is (`FleetDaemon.usage`, mirroring `.status`'s own
`_last_team_events` read). `RoleSteerCard.svelte` renders a one-line usage
summary per role (`N tokens / ceiling · $cost / $ceiling`, styled as an
alert once either axis is at or past its ceiling) and its existing
Approve/Reject hint text now names KAN-1712 alongside KAN-1711 — the panel
itself needed no new logic, since it already renders unconditionally for
`status === "blocked"` (ADR-0016's own "harmless if it's just the ordinary
steering pause" design), and a budget-triggered block is mechanically
identical to an approval-gate block from the operator's own side.

## Alternatives considered

| Option | Why not |
|--------|---------|
| A per-model pricing table to estimate kopicode's own dollar cost from its token total | Speculative and stale by construction — OpenRouter's own per-model rates drift, kopicode's own `-model` flag is fully operator-chosen, and a wrong number that *looks* authoritative is worse than an honestly-absent one. Named as a real, accepted gap, not solved here. |
| A lifetime/calendar-window budget, summed across every run a project has ever started | Real future work, but nothing has proven it's the granularity operators actually want over "catch one runaway round" (KAN-1712's own framing), and it needs its own cross-run aggregation story this run-scoped ceiling doesn't. Deferred per ADR-0002's own "don't build ahead of a proven need." |
| A new `UsageRecorded` episodic event, separate from `DelegationCompleted`/`Refused`/`Failed` | Would work, but adds a second event a reader has to correlate back to the round it belongs to, when the round's own outcome event is already the natural place usage lives — ADR-0004's "no parallel transcript" applies here too, not just to whole stores. |
| A dedicated wire/event type for the budget hard stop, composed alongside `ApprovalDecision` | Exactly the composition ADR-0016 already found collides on satay's own bare-ordinal wait identity. Forcing budget-exceeded through the *existing* `ApprovalDecision` wait sidesteps this by construction, the same way `require_approval` replacing (not composing with) steering already does. |

## Consequences

An operator can now see, per role, exactly how many tokens (and, for
Claude Code, exactly how many real dollars) a run has burned so far, and
configure either a token or a cost ceiling — per project, reviewed once —
that forces the run to stop and ask before going further, using the
identical Approve/Reject mechanism KAN-1711 already built and the identical
dashboard panel that already renders for it. kopicode's own cost stays
`None` always; this is an honest, named gap (kopicode's own headless
surface reports no dollar figure at all, verified against its source, not a
missing feature of this slice) rather than a fabricated estimate. The
budget ceiling is run-scoped, not lifetime — a real, deliberate
simplification, not an oversight (see Decision). Live-verified end to end
in a real browser against a real `cuttlefish serve` (an isolated `$HOME`,
never the operator's own `~/.cuttlefish/projects.db`): registering a
project with `max_tokens=0`, starting it against `_INVALID_OPENROUTER_KEY`
(kopicode's own real-401-no-real-cost fixture, ADR-0016's own precedent),
watching the role render `BLOCKED` with a `0 / 0 tokens` usage line styled
as over-budget, approving it through the dashboard, and confirming
`ApprovalDecision`/`TaskFailed` land correctly in the event log — not just
build-checked.
