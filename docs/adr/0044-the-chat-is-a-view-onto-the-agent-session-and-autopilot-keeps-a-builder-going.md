# ADR-0044: The chat is a view onto the agent session, and Autopilot keeps a builder going

- Status: Accepted
- Date: 2026-10-22
- Deciders: Jian

## Context

ADR-0032 (2026-10-11) made one long-lived agent session per project the primary object, with a chat-like thread and the
raw terminal as two views of it. What was built is the tmux session (ADR-0034), the terminal (ADR-0035), artifacts
(ADR-0036) and approvals in Needs you (ADR-0040). The chat thread was not, so the product has two unsynced ways to
prompt: the terminal, and the project page's "Start team" box. A hands-on session on 2026-10-22 found the second one
wrong for the main use: a started builder runs one headless round and stops to ask again, the agent's reply is a
one-line summary, and a follow-up starts a fresh conversation unless `CUTTLEFISH_RESUME=1`. The person wants to start a
builder, have it plan, and have it run for hours or days, with an interface like the Claude Code or Codex VS Code
extension, plus images, video and (later) designs.

Claude Code's Remote Control (checked against `code.claude.com/docs/en/remote-control`) is the same idea for Claude only:
the session stays on the PC, and claude.ai/code and the Claude app show the one conversation, synced across terminal,
browser and phone ("send messages from your terminal, browser, and phone interchangeably"), relayed through Anthropic's
API with push notifications. It cannot be embedded here (claude.ai login, Claude only).

## Decision

1. **The centre of a project is a chat thread rendered from the agent's own session record**, beside the terminal of the
   same tmux session. Input from either goes into that session, so there is one conversation. Records: Claude Code's
   transcript JSONL, kopicode's `events.jsonl`, Codex's app-server events (`docs/research/session-control-spike.md`).
2. **Two layouts, both:** a Chat tab (plan rail, thread, artifact canvas) and an Overview (goal, progress, a "While you
   were away" digest, a timeline). A phone gets one column. `docs/design/chat-rework/` is the mockup.
3. **Autopilot is on by default for a new builder.** The agent plans, the person approves a plan card, and the daemon then
   keeps the session going: when the agent stops with plan steps left it sends "continue", bounded by a time limit, a token
   limit, a maximum number of nudges and stuck detection. The person is interrupted only when the agent is blocked or needs
   a permission. Pause and Stop always work.
4. **"Start team" is replaced by "Give the builder a goal".** The round and team workflow stays under Advanced.
5. **The canvas shows what the builder leaves in the artifacts folder:** images now, then video, then interactive HTML in
   a sandboxed iframe.
6. **The terminal is dark by default** with its own tokens and a Match app / Dark / Light switch, and a **Shell** terminal
   (the person's shell in the project folder, no agent needed) sits beside the agent's, behind the same Terminal tier, with
   the filtered agent environment and no project secrets.
7. **Order:** Claude Code, then Codex, then kopicode.

This supersedes the "Start team" form as the main way to start work. It does not remove the workflow (ADR-0001 and
ADR-0007 hold for it) and it builds on, not replaces, ADR-0032.

## Consequences

- The thread depends on each tool's own record, whose format is the tool's and not documented as stable. Each needs a
  parser and a test against a real session, and a clear message when the format is not understood.
- Typing into a TUI can land in a dialog. The input path must know busy, idle and dialog-open (hooks, transcript) and say
  so, and use bracketed paste.
- ADR-0004 says what a person reads back derives from the episodic journal. The thread is derived from the tool's record
  plus journal events (requests, checkpoints, stops); whether the journal also keeps the agent's messages is open.
- Autopilot makes an agent act for hours without a person. The limits, stuck detection, a visible Pause and Stop, and the
  never-allowed list (which holds in every mode) are what keep that safe; none has been run against a real long run yet.
- Typed access to a shell (the Shell terminal) bypasses the consent policy, as the agent terminal does (ADR-0035); the
  token or password session and the Terminal tier are the whole guard.

## Open

- Whether the Autopilot supervisor is a satay workflow (durable across a daemon restart, ADR-0001) or a daemon loop with
  its state in the episodic journal. The slice that builds it decides and updates this ADR.
- The permission mode a builder starts in under Autopilot, and what stuck means for an interactive session.
- Whether the agent's messages are also journaled.
