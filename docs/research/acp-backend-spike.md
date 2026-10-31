# ACP backend spike (CUT-232)

Question: can cuttlefish drive any Agent Client Protocol agent through the `AgentBackend` seam, without
a new protocol (ADR-0003, ADR-0005)? Paseo's "any ACP agent" claim is the reason to ask
([fleet-manager-landscape.md](fleet-manager-landscape.md)).

**Verdict: go, as a fourth backend, about 5 to 8 days to ship.** ACP is an existing surface and it
carries everything the seam needs. The prototype is `src/cuttlefish/agents/acp.py` (about 300 lines, not
registered, not selectable), tested against `tests/fixtures/fake_acp_agent.py`.

## What the protocol gives (from agentclientprotocol.com, read 2026-11-01)

- **Transport.** JSON-RPC 2.0, newline-delimited, over the agent subprocess's stdio. Stdio MCP is
  required of every agent; HTTP/SSE are optional capabilities.
- **Setup.** `initialize` (`protocolVersion: 1`, client and agent capabilities, `authMethods`), then
  `session/new {cwd, mcpServers}` returning `sessionId`. `session/new` can fail with `auth_required`
  until the client runs `authenticate`.
- **Streaming.** `session/prompt {sessionId, prompt: ContentBlock[]}` is one turn; the response carries
  `stopReason` (`end_turn`, `max_tokens`, `max_turn_requests`, `refusal`, `cancelled`). Meanwhile
  `session/update` notifications stream `agent_message_chunk`, `agent_thought_chunk`, `tool_call` /
  `tool_call_update` (`toolCallId`, `title`, `kind`, `status`, `locations`), `plan`, `usage_update`
  (`used`, `size`, optional `cost`), mode and command updates.
- **Permissions.** The agent calls `session/request_permission {sessionId, toolCall, options[]}` on the
  client; options have `kind` `allow_once | allow_always | reject_once | reject_always`; the answer is
  `{outcome: {outcome: "selected", optionId}}` or `{outcome: {outcome: "cancelled"}}`. The request is
  a plain JSON-RPC request, so it can be held open for a person exactly like kopicode's consent. Tool
  `kind` (`read`, `edit`, `delete`, `move`, `search`, `execute`, `think`, `fetch`, ...) is a coarse
  label; a command's text is only in `title` or the optional `rawInput`, which is agent-chosen.
- **Resume.** `session/load {sessionId, cwd, mcpServers}` when `agentCapabilities.loadSession`; the
  agent replays history as `session/update`s before answering. `session/resume` (no replay), `list`,
  `fork`, `close` are further optional `sessionCapabilities`.
- **Cancel.** `session/cancel` notification; the client answers pending permissions `cancelled`, the
  agent ends the prompt with `stopReason: cancelled`.

## What the prototype does

Maps onto `DelegationOutcome`: text chunks to `summary`, completed edit locations to `edited_paths`,
tool updates to `ToolCallRecord`, `usage_update` to `tokens` and `cost_usd`, the session id to `record`,
`refusal` or a denied permission with no edit to `refused`. Permissions go through the shared
`command_decider` for `execute` (so the never-allowed list holds in Auto and an asker can be wired);
edits inside the root are allowed, everything else is rejected with the agent's own `reject_once`
option. A timeout sends `session/cancel`. A replayed `session/load` history is not counted as the
answer. Tests (10) cover streaming, edit, denial, allowlist, never-allowed, timeout/cancel, load,
auth-required, missing binary, no sandbox.

## The one real agent

Installed, as authorised, into `<scratchpad>/acp-real` (its own empty directory, no global install):
`@agentclientprotocol/claude-agent-acp` **0.99.1** from the npm registry (repo
github.com/agentclientprotocol/claude-agent-acp; the older `@zed-industries/claude-code-acp` is
deprecated in its favour). `npm install --ignore-scripts`; the package and its Claude SDK dependency
declare no install scripts, only `prepublishOnly`.

Run with an empty environment and a scratch `HOME`, `initialize` and `session/new` worked:
`loadSession`, `session resume/fork/list/close/delete`, image and embedded-context prompts,
`mcpCapabilities http/sse`, `authMethods: []`, and five modes (`default`, `acceptEdits`, `plan`,
`auto`, `bypassPermissions`).

**Finding to treat as a warning:** it reported an authenticated account (`_auth/status_update`) even
though the environment held no key and `HOME` was a scratch directory, so it located the operator's
existing Claude login some other way (likely the wrapped native CLI resolving the real home from the
passwd entry). No `session/prompt` was sent, so no model call was made. An ACP agent that wraps a
logged-in tool therefore brings that tool's ambient credentials with it, and the credential broker
(ADR-0031) cannot hide them. The fake agent covers the rest (permissions, cancel, load).

## Gaps before it could ship (the estimate)

1. Registry, `CUTTLEFISH_AGENT_BACKEND=acp:<name>`, per-project agent command config, `PATH` check at
   team start, journal `DelegationStarted.backend`. (1 day)
2. Real-agent run with a key to learn each agent's quirks: which `kind`/`rawInput` it sends for shell
   commands (the policy matches on command text, and only `title` is guaranteed), `allow_always`
   semantics, whether `session/load` or `resume` is better for continuing a role. (2 to 3 days)
3. `authenticate` handling and a visible "needs login" state; ambient-credential policy per agent. (1 day)
4. Client `fs` and `terminal` methods if an agent expects them (the prototype advertises none). (1 day)
5. Streaming into the dashboard and live steer/queue (`promptQueueing`, `steering` are agent `_meta`,
   not standard), handoff for checkpoints, sandbox provider support (stdio into a container). (2 days)

Risks: ACP is young (the claude adapter's package was renamed during this card's lifetime); agents differ
on optional capabilities, so every backend needs a capability-checked path; permission detail is
agent-authored, so command-level policy is weaker than on kopicode, Codex `app-server` and stream-json.
