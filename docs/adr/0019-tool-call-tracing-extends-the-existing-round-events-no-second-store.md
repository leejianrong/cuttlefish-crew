# ADR-0019: Tool-call-level tracing is a new episodic event journaled alongside each round's own verdict — not a second, heavier journal

- Status: Accepted
- Date: 2026-09-28
- Deciders: Jian

## Context

KAN-1714 (CUT-E10): "Paperclip's 'ticket system': full tool-call tracing,
immutable audit log per conversation. cuttlefish's episodic journal today is
delegation-outcome-level (`DelegationStarted`/`Completed`/`Failed`), not
per-tool-call. Decide how deep to go without turning the journal into a
second, heavier product than what ADR-0004 scoped it for."

Every backend's own `classify_stream` already parses a full per-tool-call
event stream today — kopicode's `tool_call_parsed`/`tool_result`/
`permission_decided`, Claude Code's `tool_use`/`tool_result` content blocks,
Codex's `item.completed` (`file_change`/`command_execution`) — and then
throws almost all of it away, keeping only `edited_paths` and a one-line
`summary`. The data this card asks for already exists in memory at
classification time; the question was only ever how to preserve it without
breaking ADR-0004's own "no parallel transcript, no second store" rule.

Three real, per-backend parsing gaps had to be closed first, each verified
live (never assumed) before any design:

**kopicode**: `tool_call_parsed` and its own matching `tool_result` are FIFO
paired one call at a time (verified live, 2026-09-28, a real
`OPENROUTER_API_KEY`-backed run: a `write_file` call's own result always
lands before the next call's `tool_call_parsed`) — the identical ordering
guarantee the existing `edited_paths` extraction for `write_file`/
`delete_file` already relies on, now generalised to every tool. A denial's
own `tool_result` still carries a non-empty `reason` (kopicode's
`ErrorKind`) — indistinguishable from a genuine execution error by itself,
so a call is only marked `"denied"` when a `permission_decided`/`deny`
fired since the *previous* `tool_result`, consumed and reset per call.

**Claude Code**: a `tool_use` content block and its own later `tool_result`
block are joined by `tool_use_id`, not FIFO order (verified live that this
id is a real, always-present field on both sides) — used here rather than
assuming kopicode's own strict single-call-at-a-time ordering also holds,
which was never separately verified for Claude Code. A call's own `status`
starts as `"ok"`/`"error"` from its own `tool_result.is_error`, then any
call named in the *final* `result` event's own `permission_denials` list is
upgraded to `"denied"` in a second pass, once that event is known — the
identical two-signal problem kopicode has, solved with the two signals
Claude Code's own stream actually provides instead.

**Codex**: `item.completed` already carries an item's *final* state — no
`item.started`/`item.completed` pairing is needed at all (unlike the other
two backends), verified live. A rejected patch never produces a
`file_change` item in the first place (ADR-0018's own already-documented
finding), so every observed `file_change` item is inherently a landed edit;
a `command_execution` item's own `exit_code` alone distinguishes success
from failure.

## Decision

**`ToolCallRecord` (`cuttlefish.agents.outcome`) is a new, small dataclass**
(`tool: str`, `detail: str`, `status: Literal["ok", "denied", "error"]`).
`DelegationOutcome` gains `tool_calls: list[ToolCallRecord]`, populated by
every backend's own `classify_stream` alongside (never instead of) its
existing `edited_paths`/`summary` reduction — the exact same per-call data
each backend was already parsing and discarding.

**`ToolCallRecorded` (`cuttlefish.episodic.events`) is one new episodic
event type**, copied verbatim off each `ToolCallRecord`, journaled once per
entry, in order, immediately after that round's own
`DelegationCompleted`/`DelegationRefused`/`DelegationFailed` —
`cuttlefish.workflow.run_task`/`cuttlefish.team.run_team` both gain an
identical `for call in outcome.tool_calls: await journal(...)` loop right
after their existing outcome-journaling call. This is deliberately **not** a
new store, and **not** a replacement for the round's own single verdict —
it is the per-call detail underneath one round, the same relationship
`DelegationStarted` already has to the delegation it starts. ADR-0004's "no
parallel transcript" rule is held to at the *event* granularity here (one
more fact-shaped event type in the one existing journal), the same way it
already was when `HandoverWritten`/`ApprovalDecision`/`TeamResumed` were
each added in earlier slices — not reopened or loosened.

**Where this stops, deliberately, naming the boundary the card itself
raised:** no tool-call-level event feeds `cuttlefish.handover`'s own
context-compaction logic (`_texts`/`estimate_event_tokens`) — a handover's
job is to compress "progress so far" into a few sentences, and per-call
minutiae is exactly the kind of detail a handover should already be
compressing away, not preserving verbatim into a model's own next prompt.
No collapsing/grouping UI ships for the dashboard's own event log either —
each `ToolCallRecorded` renders as its own row (tinted by `status`, the same
subtle-tint precedent `HandoverWritten`'s own row already set), not a
"N tool calls, click to expand" summary. Both are real, named scope
boundaries — one more log-line type in an existing list is not "a second,
heavier product," and stopping here is the answer to the card's own "how
deep to go" question, not an oversight.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Embed `tool_calls` as a list field directly on `DelegationCompleted`/`Refused`/`Failed`, no new event type | One event's JSON payload growing unboundedly with a whole round's tool calls is closer to the "second, heavier product" ADR-0004 warns against than one small event per call; it also loses the dashboard's own per-row rendering/tinting for free, and doesn't compose with the event log's existing one-row-per-fact shape. |
| Classify every Claude Code tool call's pairing by FIFO order, matching kopicode's own approach, to avoid tracking `tool_use_id` | Never separately verified for Claude Code, and the id was right there, already carried on both sides — assuming an ordering guarantee that happened to work in one probe would be exactly the kind of "not verified live" shortcut this project's own standing discipline exists to avoid. |
| Feed `ToolCallRecorded` into `cuttlefish.handover`'s own summarisation window | Would make a routine handover checkpoint balloon with per-call noise a human or a model's own next prompt doesn't need — the round's own `DelegationCompleted.summary` already carries the compressed version of what a call accomplished. |
| A collapsible "N tool calls" dashboard summary instead of one row per call | Real, plausible future polish, but a genuine UI redesign this card's own "how deep to go" caution argues against building ahead of a proven need for — the flat, tinted row list already answers "what happened, and was anything denied or errored" at a glance. |

## Consequences

`cuttlefish show`/the dashboard's own event log now shows every individual
tool call a delegation made, not just its round-level verdict — Paperclip's
own "ticket system" bar, met at the granularity this project's episodic
journal already operates at, not a new one. All three backends' own
`classify_stream` functions gained real per-call extraction logic that was
verified live against each real binary (kopicode with a real
`OPENROUTER_API_KEY`-backed call, Claude Code and Codex both with real
authenticated runs) before merging, not just reasoned about from a
synthetic shape. `cuttlefish.handover`'s own token-budget accounting is
unaffected (an intentional non-goal, see Decision) — a long, chatty round
with many tool calls does not trip a handover any sooner than it already
would have. The dashboard's event log can now be meaningfully longer per
round than before; no pagination or collapsing was added this slice, a
named, deliberate scope boundary rather than an oversight, to revisit only
if a real run's own event volume makes the flat list genuinely hard to
scan.
