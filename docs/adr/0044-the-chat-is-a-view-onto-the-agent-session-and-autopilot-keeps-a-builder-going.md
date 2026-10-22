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

## Notes from building (2026-10-22)

- Input into a Claude Code session (CUT-173) is a tmux bracketed paste then Enter, refused while a dialog is open. The
  dialog test is two weak signals ORed (the session's hooks, the last lines of its screen), because either can be wrong and
  a message typed into a dialog is an answer. Typing needs the Terminal tier; reading the thread needs Approve.
- A goal (CUT-171) is the tool's own first-message argument, never typed, so the first message cannot land in a dialog.

## Decided while building Autopilot (CUT-174, 2026-10-22)

These were the open questions of the first version of this ADR.

1. **The supervisor is a daemon-side supervisor, not a satay workflow, and its state is the journal's.** ADR-0032 already
   said a session-first path need not be a workflow, and nothing here needs one: the builder is a process in tmux that
   outlives the daemon, so there is no run for satay to resume. What the supervisor owns is a small state, kept as the
   project's last `AutopilotUpdated` event in the episodic journal (a whole snapshot per change, under the session scope,
   redacted at write time), read back the first time a project is touched after a restart. It is driven by the agent's
   `Stop` hook; a `UserPromptSubmit`, `PreToolUse` or `PostToolUse` that arrives within two seconds cancels a pending nudge,
   so a message the person typed right behind the stop is not trampled. `cuttlefish/autopilot.py` holds the policy as one
   pure function; `fleet/autopilot.py` feeds it facts and acts.
2. **The plan is a checklist file, `.cuttlefish/artifacts/plan.md`, not Claude Code's plan mode.** The builder is told (in
   Claude Code's appended system prompt; on the end of the goal for Codex, which has no such flag) to write `- [ ]` steps,
   wait for approval, then tick `- [x]` as it goes. A file works the same for every backend, is an artifact the dashboard
   already shows, and a person can edit it. Claude Code's own todo tool was not in the install checked, and plan mode would
   tie approval to one tool's UI. Cost: it depends on the agent following the instruction; no checklist means no approval
   and no nudging, and the rail says so.
3. **What "stuck" means:** the builder stopped three nudges in a row without ticking a step, or the plan has no steps.
   Both end the nudging and show as "Stuck" with a Carry on button. Repeated environment failures (`stuck.py`) are not
   used yet. It is surfaced in the rail, the Overview and a banner, not as a Needs you card.
4. **Limits end the nudging, never the agent mid-step:** time (24 h from approval), tokens (5 M since approval: input,
   output and cache writes from the transcript, cache reads left out) and nudges (200). All three are checked when the
   builder stops. Resuming after a limit lifts it and restarts the clock.
5. **A builder under Autopilot starts in the project's own permission mode**, with the same hooks and Needs you cards as
   any session (ADR-0040). Autopilot never answers a permission and never types into a dialog: its two messages (the
   approval and "continue") go through the same guarded send as a person's (CUT-173). A dialog open means the nudge is not
   sent and the next stop tries again.

## Open

- Whether the agent's messages are also journaled (so history survives a deleted transcript).
- Autopilot has been run against two short real Claude Code sessions (2026-10-23: plan-first followed, checklist ticked, `Stop`
  hook fired, "continue" worked) but not a long one. See `agent_docs/known-gaps.md`.
