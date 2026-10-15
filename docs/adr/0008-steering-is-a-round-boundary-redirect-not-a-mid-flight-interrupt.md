# ADR-0008: Steering redirects at a delegation round's boundary, not mid-flight; delivered over satay's control API

- Status: Accepted. Partly superseded by [ADR-0033](0033-a-codex-turn-in-flight-takes-a-steer-through-turn-steer.md) for a Codex turn steered through the fleet daemon
- Date: 2026-09-21
- Deciders: Jian

## Context

ADR-0007 shipped slice C's team-concurrency half and named the steering half as
"unblocked but not yet wired into cuttlefish" once `satay>=0.2.0` (`satay.control
.run_app`, ADR-0046 there) was installable. That dependency landed 2026-09-21
(`docs/QUESTIONS.md` Q42's update) — this ADR is the design pass ADR-0007 deferred:
what `cuttlefish run --steerable`, `cuttlefish steer <task-id> "<message>"`, and the
workflow-shape change inside `run_task`/`run_team` actually look like.

Two things this design pass found reshape it before any code was written, both of
which contradict the original sketch ("race a short `wait_for_event` against normal
delegation progress" — this project's own working assumption, PLAN.md's Open risks,
carried into this slice's own kickoff instructions) rather than confirm it.

**Neither backend's headless surface accepts input after it starts.** Verified
directly from source, not assumed: `cuttlefish.delegate.kopicode.run_kopicode` and
`cuttlefish.delegate.claude_code.run_claude_code` both invoke their binary with the
task text as a single argv positional (`kopicode run --print <text>` /
`claude -p <text> --output-format stream-json`), read `stdout`/`stderr` to
completion, and never open a `stdin` pipe. There is no channel an already-running
invocation could read a new instruction from. Building one would mean inventing a
live, bidirectional protocol on top of a tool that only offers a one-shot,
argv-in/stream-out surface — exactly what ADR-0003 and ADR-0005 already rule out
("wraps its own tool's existing headless surface as it exists... never inventing a
shared wire format"). A backend's own live-input capability (if it ever supports
one) is what would license anything more, not a design invented on cuttlefish's
side.

**Racing `wait_for_event` against an in-flight task via `satay.gather` is not a
proven, or even documented, composition.** Read directly out of
`satay/replay/engine.py`: a `wait_for_event` miss raises `WorkflowParked`, a
`BaseException` (not `Exception`) whose only handler sits at the very top of the
per-run drive loop (`engine.py:1022`) — it exists to unwind the *entire* workflow
drive and release the coroutine from memory (`docs/cookbook/timers-events.md`:
"Parking Is The Whole Feature"). `satay.gather`'s own docstring enumerates its
members as "task calls, nested `map` calls, and `start_child` calls" — never
`wait_for_event`. Nothing in satay-runtime's test suite or cookbook mixes the two.
Whether a `WorkflowParked` raised inside one `gather` member correctly parks only
that member, or corrupts the composite's fail-fast/collect bookkeeping (`gather`'s
own `_settle_composite` treats a member's exception as *that member's* failure,
which `WorkflowParked` was never designed to be), is genuinely unverified. Building
cuttlefish's steering feature on an unverified composition of satay's own primitives
would repeat the exact mistake this session's own mid-flight correction (recorded in
memory, `vision-cuttlefish-crew-pivot`) already caught once for this same slice —
substituting an appealing-sounding design for what the actual primitives support.
The right move, per the standing instruction that satay's own roadmap follows
cuttlefish-crew's needs (`docs/QUESTIONS.md` Q33), would be asking satay-runtime for
a scoped, verified racing primitive if this project ever genuinely needs one — not
assuming it already exists.

Given both findings, true mid-invocation interruption (killing a running backend
subprocess the instant a message arrives, mid-reasoning) is out of reach this slice
without either crossing the no-new-protocol boundary or asking satay-runtime for a
new capability neither confirmed need justifies yet. What *is* reachable with
today's verified primitives, used exactly as their own cookbook demonstrates them
(a plain, sequential, non-composed `wait_for_event(..., timeout=...)` resolving to
`None` on a miss), is redirection at the boundary between one delegation round and
the next.

## Decision

**`run_team`'s polling stays outside its `satay.gather`, never nested inside a
member.** The unverified-composition finding above is about `wait_for_event`
appearing *anywhere* underneath a `gather` composite's call stack, not only about
racing it against a sibling member directly — a role's own per-role coroutine
calling `wait_for_event` internally, if that coroutine is itself one `gather`
member, hits the identical unverified `WorkflowParked`-under-`gather` risk.
`run_team`'s round loop therefore lives one level up: each round still gathers only
plain `delegate_to_agent_backend` calls (`satay.gather`'s exact, already-proven
`return_exceptions=True` shape ADR-0007 shipped), and only *after* that `gather`
resolves does the workflow loop **sequentially** over the roles that just finished,
one bare top-level `wait_for_event` call per role, to decide which roles get
another round. Roles needing one loop together into the *next* `gather` call; roles
that didn't finalize this round. This keeps every `wait_for_event` call a plain,
top-level, sequential await — the identical safe shape `run_task` uses — and never
puts one underneath a composite.

**A task's delegation becomes a loop of rounds, not one call — but only when the
caller opts in.** `TaskInput`/`RoleInput` gain `steerable: NotRequired[bool]`
(default `False`). When `False`, `run_task`/`run_team` run byte-for-byte as they do
today: one `delegate_to_agent_backend` call, then straight to
`maybe_handover`/`TaskCompleted`/`TaskFailed` — the same "an event written before
this slice decodes unchanged" discipline ADR-0007 already held for its own new
`role` field. When `True`, after each round's outcome is journaled exactly as today,
the workflow makes **one** plain, sequential
`satay.wait_for_event(SteeringMessage, key=steer_key, timeout=DEFAULT_STEERING_GRACE)`
call — not gathered against anything — before deciding whether to finalize. A miss
(`None`) finalizes with that round's own outcome, identically to the non-steerable
path. **The poll only ever follows a real, recorded `DelegationOutcome`
(completed/refused/failed) — never a `DelegationError`** (`run_task`'s own
infra-level exception path, or `run_team`'s equivalent collected
`satay.TaskFailedError`): a binary missing or a malformed stream is not something a
steering message should paper over, the same "caught, journaled, surfaced as a real
failure, not silently retried" posture `docs/QUESTIONS.md` Q16 already holds for
every delegation failure. A hit starts one more round: a new `delegate_to_agent_backend` call whose
`task_text` is the original text plus a short log of prior rounds' own outcomes and
the operator's own message text, so a fresh backend invocation (which has no memory
of the last one beyond whatever it already wrote to the checkout) knows what
happened and what changed. `DEFAULT_STEERING_GRACE` is a short constant (5 seconds)
— long enough that an operator's `cuttlefish steer` call sent right as a round
finishes isn't lost to a race with finalization, short enough that a task nobody is
steering pays a bounded, small tax once per round rather than a real wait.

**`SteeringMessage` is one dataclass, doing two jobs at once, not two parallel
event types.** `cuttlefish.episodic.events.SteeringMessage(text: str, role: str |
None = None)` is both satay's wire payload type (`wait_for_event`/`send_event`
derive the inbox key's type name from `SteeringMessage.__module__` +
`__qualname__` — ADR-0004's episodic store and satay's own `.satay/` journal are
still two separate storage systems, never conflated — but the *Python type*
describing "an operator sent this text" has no reason to exist twice) and the
episodic event journaled the moment a round consumes one (`await journal(task_id,
steer_event)`, same call every other event in this module already goes through).
`role` follows ADR-0007's own discipline: always `None` for a plain `run_task`,
tagged for a team role, so `cuttlefish show`/a future dashboard read every steering
message the same way they read every other event.

**Delivery key scheme**: `key = task_id` for a plain `run_task` (there is only one
delegation stream to redirect); `key = f"{task_id}:{role}"` for a `run_team` role
(steering one role must never wake another's poll). `cuttlefish steer <task-id>
"<message>" [--role NAME]` resolves the same key on the sending side —
`--role` is required for a task started via `run-team --steerable`, and rejected
(there's nothing to disambiguate) for a plain `run --steerable` task.

**Delivered over `satay.control.run_app`, exactly as ADR-0007 already scoped.**
`cuttlefish run --steerable`/`run-team --steerable` open
`async with satay.control.run_app() as app:` instead of the bare `satay.run_app()`
both commands use today, print `{"task_id": ..., "steering": {"base_url": ...,
"token": ...}}` to stdout *before* blocking on `handle.result()` (so a second
terminal has something to act on immediately), and write the same pair to
`.cuttlefish/steering/<task_id>.json` — a `finally` block removes it once the run
reaches a terminal state. This file is the only reason `cuttlefish steer` doesn't
need the operator to copy a token by hand: it reads `base_url`/`token` from that
file, builds the request, and is otherwise a thin, synchronous HTTP client — one
`POST {base_url}/runs/{task_id}/events` (`SendEventBody`'s own documented shape:
`event_type`, `key`, `payload`), header `x-satay-token: <token>` (satay's own
`TOKEN_HEADER`, ADR-0014), body `{"event_type":
"cuttlefish.episodic.events.SteeringMessage", "key": <resolved key>, "payload":
{"text": <message>, "role": <role or null>}}`. Python's own `urllib.request` is
enough for one synchronous JSON POST from a short-lived CLI invocation — no new
HTTP-client dependency for a call this small, the same "cuttlefish doesn't own this
kind of primitive, don't reach for a library to describe one request" reasoning
`cuttlefish.delegate.subprocess_env` already applies to its own small surface.

**A local pointer file, not a registry service.** `.cuttlefish/steering/` holding
one JSON file per in-flight steerable task is the whole "how does a second process
find this run" mechanism — consistent with this project's whole posture so far (no
daemon, no long-lived server component beyond the one `run_app`/`control.run_app`
block a `cuttlefish run` invocation already owns). A crashed `cuttlefish run
--steerable` that never reaches its `finally` leaves a stale pointer file behind;
`cuttlefish steer` treats a connection failure against a stale entry as "this task
is no longer steerable" (a clear error, not a hang) rather than trying to detect
staleness proactively — the same accepted-risk posture ADR-0002 already takes
elsewhere for a single-operator trust model.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Race `wait_for_event` against the delegation task via `satay.gather`, as originally sketched. | Unverified composition (`WorkflowParked` is a whole-drive unwind signal, not a per-member one) — the core finding of this ADR. Revisit only behind a real satay-runtime capability request, the same discipline this project already used for the control API itself (ADR-0046). |
| Kill the running backend subprocess the instant a message arrives, then restart with amended text. | Needs `run_kopicode`/`run_claude_code` to kill-on-cancellation as reliably as they already kill-on-timeout (today they only do the latter — a real, separate gap, not fixed by this ADR) *and* a safe way to trigger that cancellation from outside the coroutine's own sequential control flow, which is exactly the unverified racing primitive the previous row already rejects. Two open problems stacked, not one. |
| Loop unconditionally (poll after every round even when nobody asked for steering). | Taxes every existing `cuttlefish run` with a `DEFAULT_STEERING_GRACE`-long wait per round for a feature that was never requested — a real, avoidable latency regression for the common case. `steerable` as an explicit, default-`False` opt-in keeps every pre-existing caller's behaviour byte-for-byte unchanged, the same discipline `role: str | None = None` used in ADR-0007. |
| A second, satay-independent event type purely for the episodic journal, distinct from the one satay's wire protocol uses. | Two names for "an operator sent this text during this task" is accidental complexity ADR-0004's storage-separation reasoning doesn't actually require — that ADR separates *where journals live*, not what Python type may describe an event flowing through both. |
| A registry service (even a tiny one) tracking every steerable task's `base_url`/token, instead of one file per task. | Real infra for a problem one JSON file per task already solves, at a scale (one operator, their own machine, one task at a time steered) this project isn't past yet — the same "don't build ahead of a proven need" posture ADR-0002 already holds to. |

## Consequences

An operator can send `cuttlefish steer <task-id> "<message>"` while a task or team
role is running and have it change what the agent does next — genuinely, not
read-only — bounded by however long the round in flight takes to finish, not
instant. That bound is named honestly here rather than discovered as a surprise
later: a single real coding-agent invocation can run minutes, and a message sent
mid-invocation waits for that invocation's own natural end before it's ever seen.

A non-steerable task (today's entire existing surface, and the default for the new
flag) pays nothing new — no extra `wait_for_event` call, no `.cuttlefish/steering/`
file, no behavioural change at all. It does cost one real, unconditional dependency
widening: `satay.control.run_app` needs FastAPI/uvicorn, gated behind satay's own
`satay[studio]` extra (ADR-0046's "studio-only, imports lazily") — plain `satay`
does not install them. `--steerable` is an ordinary, always-available CLI flag, not
an opt-in extra of cuttlefish's own, so cuttlefish's own pin moves from
`satay==0.2.0` to `satay[studio]==0.2.0` rather than cuttlefish inventing a second
packaging layer (a `cuttlefish[steering]` extra) purely to defer a dependency satay
already gates cleanly on its own side. `docs/QUESTIONS.md` gets a new entry for this
design pass; `docs/PLAN.md`'s Open risks entry for steering is resolved (not
deferred) by this ADR, with the "instant mid-flight interrupt" aspiration named as
real, deliberately out-of-reach future work — contingent on either a backend
gaining a genuine live-input surface, or a verified, satay-runtime-provided racing
primitive, neither of which exists today and neither of which this slice invents
speculatively ahead of a proven need.

The `run_kopicode`/`run_claude_code` cancellation gap this ADR's research surfaced
(kill-on-timeout exists, kill-on-`CancelledError` doesn't) is named here as a real,
pre-existing, and — given this ADR's own round-boundary design never cancels a
live subprocess — currently inert gap: nothing this ADR builds exercises it. Left
unfixed for this slice rather than bundled in, since fixing it usefully needs a
caller that actually cancels mid-flight, which this design deliberately isn't yet.
