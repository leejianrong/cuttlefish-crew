# Codex `app-server` spike (V4-L, 2026-10-08)

Research only, no product code. The question: can cuttlefish-crew put live prompts, a really enforced
command list, Stop and resume on Codex by driving `codex app-server` instead of `codex exec`?
Short answer: yes, on everything we tried. The protocol is the one ADR-0003/0005 allow (Codex's own
headless surface, normalised into `DelegationOutcome` on our side).

## Method and limits

- `codex-cli 0.161.0`, ChatGPT login, `gpt-5.6-luna` at `low` effort, tiny tasks (a few commands each),
  about 14k tokens a turn. stdio transport only (`ws://` and `unix://` exist, not tried).
- A 60-line Python JSON-RPC driver over `codex app-server` stdin/stdout, one scratch git repo per scenario.
  The protocol came from `codex app-server generate-json-schema` (the schema is large: 100 client
  requests, 10 server requests, about 80 notifications). The app-server is marked experimental, and
  `initialize` needs `capabilities.experimentalApi: true` for some fields.
- One run per scenario. Model behaviour (does it ask? does it escalate?) varies, so "seen once" is
  not "always".

## What the protocol looks like

Newline-delimited JSON-RPC, without a `"jsonrpc"` field. Handshake: `initialize` request, then an
`initialized` notification. Then:

| Need | Message | Seen |
|---|---|---|
| start a conversation | `thread/start` `{cwd, approvalPolicy, sandbox, model}` | yes; returns `thread.id` |
| run a round | `turn/start` `{threadId, input:[{type:"text",text}], effort, approvalPolicy, sandboxPolicy}` | yes; returns at once with `status: inProgress`, finishes with a `turn/completed` notification |
| stop a round | `turn/interrupt` `{threadId, turnId}` | yes: answered in 10 ms, `turn/completed` with `status: "interrupted"` right after, the running `sleep 120` was killed (no orphan) |
| redirect mid-round | `turn/steer` | in the schema, not tried |
| resume | `thread/resume` `{threadId, cwd, approvalPolicy, sandbox}` in a **new process** | yes: context kept (the agent recalled the command from the interrupted turn) |
| list/read history | `thread/list`, `thread/read`, `thread/turns/list` | in the schema, not tried |

`approvalPolicy` is `untrusted` | `on-request` | `never` | `{granular: {...}}`. `sandbox` (thread) or
`sandboxPolicy` (turn) is `read-only` | `workspace-write` | `danger-full-access`; both can be overridden per turn,
and a turn's override applies to the turns after it.

## Approvals: how a command gets a person's answer

Server-to-client **request** (it has an `id`, we reply with a plain JSON-RPC response):

```
item/commandExecution/requestApproval  params {kind:"command", threadId, turnId, itemId, command,
  cwd, commandActions, reason?, proposedExecpolicyAmendment?, availableDecisions, startedAtMs}
-> {"decision": "accept" | "acceptForSession" | {"acceptWithExecpolicyAmendment":{...}} | "decline" | "cancel"}
```

`decline` refuses the command and the agent carries on (the item completes with `status: "declined"`,
the agent said "rejected by environment"). `cancel` refuses it and ends the turn. `availableDecisions` lists
`accept`, an amendment and `cancel` but `decline` worked all the same. After the answer the server sends
`serverRequest/resolved`. File edits have `item/fileChange/requestApproval` (same shape, not triggered
here because `apply_patch` inside `workspace-write` ran without asking), and there are
`item/permissions/requestApproval`, `mcpServer/elicitation/request` and the legacy `applyPatchApproval`
and `execCommandApproval`.

Findings that matter for the design:

1. **`untrusted` prompts for every command**, including `ls`. Four commands gave four requests, all
   carrying the exact command line and cwd. That is the hook for a really enforced allowlist: our handler
   matches the command against the shared never-allowed list and the project's allowed commands
   and answers `accept`, `decline` or asks a person. Nothing needs to be inferred from stderr.
2. **`on-request` + `workspace-write` prompts for nothing the model does not choose to escalate.**
   `curl` ran (no network, so no output), `git commit` failed with `.git` read-only, no request. The
   agent reported the failure. When told to, the model asked: a `requestApproval` with `reason: "The sandbox
   blocked the requested git commit; may I retry it with escalated permissions?"`, and an accept let it
   commit. So `on-request` leaves the policy to the model; it cannot be the enforcement.
3. **An approved command runs outside the sandbox.** Under `untrusted`, `accept` on
   `touch /tmp/spike_outside.txt && git commit --allow-empty` created the file in `/tmp` and committed.
   So the sandbox is no longer a backstop once we accept; the never-allowed list (writes outside the root,
   `sudo`, `rm -rf` outside the root, `git push --force`, `curl | sh`) must be checked in our handler on
   the command text *and* cwd, and the existing "reads the command line as text" gap applies. This is the
   biggest design constraint.
4. The accepted `git commit` worked, which fixes the "commits sometimes fail" gap: under `untrusted` a
   commit is just an approved command.
5. `proposedExecpolicyAmendment` is how Always allow could map ("allow `git commit --allow-empty`" as an
   argv prefix), but cuttlefish keeps its own project-wide allowed commands, so we would answer plain
   `accept` and keep our own list (ADR-0028).

## User questions

`item/tool/requestUserInput` (server request) carries `questions: [{id, header, question, options:[{label,
description}], isOther, isSecret}]`, `isBlocking`; the answer is `{answers: {<id>: {answers: ["Blue"]}}}`.
**It exists only when the feature is on**: with the default config the tool is "unavailable in this mode"
(the agent said so). With `--enable default_mode_request_user_input` (feature flag, listed "under
development") the agent asked, we answered, and the agent used the answer. `isBlocking` was `false` and
`autoResolutionMs` null in that run, so we have not seen how long the server waits for an answer. Treat this
as the least stable piece; `isSecret` means a person may type a secret, which the journal must scrub.

## Events and usage

- `item/started` and `item/completed` for `userMessage`, `reasoning`, `commandExecution` (command, cwd,
  status `completed`/`failed`/`declined`, exit code, output), `agentMessage` and file changes.
  **The final agent message** is the `agentMessage` item with `phase: "final_answer"` (also the only item
  in `turn/completed.turn.items` when `itemsView` is `"summary"`). This is what `codex exec` never gave us.
  `item/agentMessage/delta` streams it.
- `thread/tokenUsage/updated` after each model call: `tokenUsage.total` and `.last` with `inputTokens`,
  `cachedInputTokens`, `outputTokens`, `reasoningOutputTokens`, `totalTokens`, plus `modelContextWindow`
  (258,400 here). So context pressure and a token ceiling become possible for Codex, live, not only after
  the round. No cost figure anywhere (ChatGPT login), so `cost_usd` stays `None`.
- `turn/started`, `turn/completed` (`status`: `completed`/`interrupted`/..., `durationMs`, `error`),
  `thread/status/changed`, `account/rateLimits/updated`, `configWarning`, `mcpServer/startupStatus/updated`.
- `command/exec/outputDelta` and `item/commandExecution/outputDelta` for live command output.

## The open issues

- **openai/codex#14192** ("emits approval requests but lacks a strict approval response RPC"): closed
  as not planned. It does not bite: the response to a server request *is* the response RPC, and we did it
  that way in every scenario above. It describes a bridge that wanted a separate method.
- **openai/codex#21982** ("sandbox_permissions approval doesn't get surfaced through app-server"): open.
  Reported: an escalation shows in the transcript but no `requestApproval` reaches the client, so the turn
  stalls until a 300 s timeout. **Not reproduced**: our one escalation (git commit) arrived as a request.
  Not ruled out for other escalation kinds, so the build needs a per-turn timeout and an interrupt (which
  works) so a stalled turn cannot hold a role forever.

## Not tried

Unanswered approval (how long the server waits), `turn/steer`, `ws://`/`unix://` transports, a
shared long-lived `app-server` across roles (one process per round is the simple shape; a role's thread
persists on disk and `thread/resume` reattaches it), file-change approvals, `read-only` + `untrusted`,
`granular` policies, network access, a second concurrent thread, and compaction behaviour. `codex
exec-server`, `codex queue` and `codex resume` were not looked at: `app-server` already covers the needs.

## Verdict for V4-M

Build it. Every gap in the `codex exec` spike has an answer in the protocol: enforced list (answer
`untrusted` approvals ourselves), Needs-you cards (a pending `requestApproval`, and `requestUserInput`
behind a flag), commits (approved commands run unsandboxed), the reply (`final_answer` item), Stop and a
round timeout (`turn/interrupt`), resume (`thread/resume`), usage (`thread/tokenUsage/updated`).
Constraints: an accepted command has no sandbox, so our check carries all the weight; the user-input
path is flag-gated and experimental; the protocol is experimental and version-pinned (record 0.161.0 and
fail with a clear message on a mismatch of the methods we use).
