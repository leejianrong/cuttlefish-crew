# Chat rework: the agent session is the primary object (design, 2026-10-22)

Agreed with Jian on 2026-10-22 after a hands-on session with the local app. Decision record: ADR-0044. Board: epic
CUT-E20 (cards CUT-169 to CUT-179, board id 23). This builds the part of ADR-0032 that was never built: a chat thread
onto the project's one long-lived agent session. Read ADR-0032 first.

## Where the mockups live

- **Canvas (how it looks):** https://claude.ai/artifact/4KNEWXka63CLsMPTgeVh2Q (a Design canvas, private to its owner;
  read it with the Artifact tool, `action: "read"`, or ask Jian to share it).
- **This folder (source for markup and copy):** `canvas.json` is the canvas index and the four `*.dc.html` files are the
  boards. They are Design Component files, so they only render inside the canvas (they need its `support.js`). Read them
  for structure, copy and exact values. They are static comps with made-up content (a `ledger` project with a Claude Code
  builder), not shipped code.

| File | Board |
| --- | --- |
| `Main.dc.html` | **Recommended.** The project's **Chat** tab: plan rail (left), thread (centre), artifact canvas (right) |
| `MissionControl.dc.html` | The project's **Overview**: goal and progress ring, "While you were away" digest, timeline, a chat drawer |
| `Phone.dc.html` | The chat on a phone (390 x 844): plan strip, inline approval card, bottom nav |
| `Terminal.dc.html` | Terminal: dark by default, **Agent / Shell** switch, **Match app / Dark / Light** theme switch |

Look: Material 3 roles from the shipped dashboard (`frontend/src/theme-tokens.css`, `docs/design/ui-redesign/README.md`).
The comps use raw hex because the canvas has no token file; the product must use the `--md-sys-*` roles, never raw hex.
Teal primary `#00696f`, coral (attention) only for "needs you". Fonts: Bricolage Grotesque (brand), Figtree (plain),
JetBrains Mono (commands, paths).

## Decisions (Jian, 2026-10-22)

1. **Both layouts.** Board 1 is the project's Chat tab; board 2's digest and timeline are its Overview. A phone gets one
   column (board 3).
2. **Chat, not rounds.** The thread is a view onto the agent's own session, shown beside the terminal of the same tmux
   session. Typing in either goes to the same agent: one conversation, so they cannot drift. Today's "Start team" form
   (a headless round that stops and asks again) goes away.
3. **"Give the builder a goal"** replaces "Start team". The round and team workflow (ADR-0001, ADR-0007) stays, under
   Advanced.
4. **Autopilot is on by default for a new builder.** The agent plans first; the person approves a plan card; then it
   keeps going for hours or days (below). It interrupts the person only when blocked or for a permission.
5. **Rich output.** The canvas shows what the builder leaves in `.cuttlefish/artifacts/`: images now (ADR-0036), then
   video and interactive HTML designs in a sandboxed iframe (CUT-175, overlapping CUT-114 and CUT-115).
6. **Terminal.** Dark by default in both app themes, with its own tokens (light app: `#1d2426`; dark app: `#070b0c`
   with a 1 px border; a 16-colour palette tuned for dark) and a Match app / Dark / Light switch remembered per browser
   (CUT-169). A **Shell** terminal runs the person's shell in the project folder with no agent needed, behind the same
   Terminal tier, with the filtered agent environment (`delegate/subprocess_env.merge_env`) and **no project secrets**
   for now (CUT-170).
7. **Order:** Claude Code first, then Codex, then kopicode.

## Autopilot, as agreed

Plan first (Claude Code's plan mode), approve on a plan card, then the daemon keeps the session going: when the agent
stops while plan steps remain (a `Stop` hook, ADR-0040) it sends "continue with the next step". Bounded by a time limit,
a token limit, a maximum number of nudges and stuck detection (`cuttlefish.stuck`). Pause and Stop always work. The
agent's own context compaction or refresh and the handoff (ADR-0042) keep a days-long run coherent; the thread shows a
divider for each. Not yet verified against a real long run; the risk to design for is a runaway agent.

## What the chat reads and writes (verified in `docs/research/session-control-spike.md`, CUT-91)

| Tool | Read (thread) | Write (input) |
| --- | --- | --- |
| Claude Code | `~/.claude/projects/<cwd with / as ->/<sessionId>.jsonl`: `user`, `assistant` (`text`, `tool_use`), `tool_result` | `tmux send-keys` / paste into the session's pane (checked); approvals through the held `PermissionRequest` hook (ADR-0040) |
| Codex | `codex app-server` events (`item/agentMessage/delta`, items); two clients can share a thread (`codex --remote`) | `turn/steer`, `turn/start` (ADR-0033), or tmux |
| kopicode | `<repo>/.kopicode/sessions/<id>/events.jsonl` (`AssistantMessage`, `ToolResult`, ...); `serve` is one client at a time | tmux (the REPL), not `serve` |

Typing into a TUI is fragile while a dialog is open: use bracketed paste, learn busy, idle and dialog-open from the hooks
and the transcript, and disable the box with an explanation instead of typing into a dialog.

Claude Code's Remote Control (claude.ai/code, the Claude app) is Anthropic's version of this for Claude only: one
conversation synced across terminal, browser and phone, relayed through Anthropic's API, with push notifications. It
cannot be embedded here (login with claude.ai, Claude only); it is an optional extra at best.

## Built so far

- CUT-169 terminal theme, CUT-170 shell terminal (2026-10-22).
- CUT-171 the goal box: the goal is the tool's own first-message argument (`claude "goal"`, `codex "goal"`), not typed
  into the terminal, and is recorded in `.cuttlefish/goal.json`. A running builder is not typed into until CUT-173
  (bracketed paste and busy/idle/dialog detection). kopicode has no first-message argument, so it is refused for now.
  The **Autopilot default** moved to CUT-174, because a toggle that did nothing would imply a supervisor that is not there.

- CUT-172 the Chat tab for Claude Code, read-only: `sessions/transcript.py` parses the transcript (`user`, `assistant`
  `text` and `tool_use`, `tool_result`, compaction and recap lines; thinking, meta, sub-agent and bookkeeping lines are left
  out), `GET /api/projects/{id}/chat?after=<byte offset>` returns what was appended and the next offset, and the page
  polls every 1.5 s while the tab is open. The path comes from the transcript path Claude Code's own hooks report (so it
  follows a `/clear`), else from the recorded session id and the folder name Claude Code derives from the project path.
  Read tier is Approve (the thread has tool output), not View. Checked against a synthetic transcript in the observed
  shape and one real transcript's line types; **not** against a live Claude Code run (a real run needs Jian's yes).

- CUT-173 the message box: `POST /api/projects/{id}/chat` (Terminal tier) pastes the text into the tmux session with
  `load-buffer` and `paste-buffer -p -r` (bracketed, line feeds kept) and presses Enter 0.3 s later (`sessions/tmux.py`,
  `fleet/daemon.py:send_chat`). Control characters are stripped first (`sessions/chat_input.py`): an ESC could close the
  paste early. Busy, idle, dialog and unknown come from the session's hooks (`ClaudeHooks.activity`: `UserPromptSubmit`,
  `PreToolUse`, `PostToolUse` mean busy, `Stop` idle, a held card or a permission left to the terminal mean dialog, a
  turn silent for 5 minutes means unknown because Esc ends a turn without a `Stop`) and from the last lines of the screen
  (a screen that looks like a dialog wins, and a hooks dialog the screen no longer shows is dropped). A dialog refuses the
  send; busy sends and says it is queued. Verified with a fake raw-mode tool in tmux that asks for bracketed paste (the
  exact bytes) and from the browser; **not** verified against a real Claude Code (Enter timing after a large paste,
  whether Claude Code queues a message sent mid-turn as assumed, its dialog wording, `SessionStart`/trust-dialog handling).

- CUT-174 the plan card and Autopilot (decisions in ADR-0044): a goal given with Autopilot on (the default from the
  dashboard) tells the builder to write a checklist to `.cuttlefish/artifacts/plan.md` and wait; on its `Stop` hook the
  supervisor moves to "plan waiting for you" when a checklist exists; **Approve the plan** sends the fixed approval message;
  from then on each `Stop` with steps left sends "continue" (after 2 s, unless the turn went on), bounded by time, tokens
  and nudges, stuck after three nudges with no ticked step, with Pause, Carry on and Stop in the rail (and on a phone, above
  the thread). State is the journal's last `AutopilotUpdated`. Checked with a fake builder and a synthetic transcript through
  the real hook script, the daemon and the browser, and then (2026-10-23) with two short real Claude Code runs: it planned first, ticked the
  checklist, the `Stop` hook fired every turn, and "continue" made it carry on. Not over hours.

- CUT-176 mission control on the Overview and CUT-179 the phone layout (see `docs/design/ui-redesign/README.md`).

- CUT-177 the chat for Codex: read from Codex's own rollout, `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (`sessions/codex_record.py`),
  not app-server events, because a tmux-hosted Codex is the TUI and the rollout is its record on disk. `item_completed` events
  (`UserMessage`, `AgentMessage`, `CommandExecution`, `FileChange`, web search, `ContextCompaction`) become the same chat items as
  Claude Code's, so the page, the canvas and the Overview timeline need no second code path. The rollout is found by folder: the
  newest one whose `session_meta` `cwd` is the project root and that was written after the session started (our `codex` and
  `codex resume --last` launches give us no session id). Read-only: there is no message box for Codex (no hooks to say a dialog
  is open), and Autopilot's token meter reads Claude's usage lines only. Checked against synthetic lines and against five real
  rollouts on this machine (counts only: 0 lines not understood); not against a live Codex run through the dashboard.

- CUT-178 the chat for kopicode, the last of the order: read from `<repo>/.kopicode/sessions/<id>/events.jsonl`
  (`sessions/kopicode_record.py`): `UserMessage`, `AssistantMessage`, `ToolCallParsed` with its `ToolResult` (by `call_id`) and
  `AskRequested` become the same chat items; provider traffic, permissions and bookkeeping are left out. The REPL in tmux makes
  its own session id, so the record is the newest `events.jsonl` written after the tmux session started. Read-only, as Codex
  (kopicode's REPL has no hook to say a dialog is open, ADR research CUT-91). Checked against synthetic lines and the two real
  sessions in this repo's `.kopicode` (0 lines not understood); not against a live kopicode REPL through the dashboard.
  All three backends now have a chat; the whole of CUT-E20's order (Claude Code, Codex, kopicode) is built.

## Build order (cards)

1. CUT-169 terminal theme, CUT-170 shell terminal (small, independent).
2. CUT-171 the goal form replaces Start team.
3. CUT-172 chat view for Claude Code (read-only), CUT-173 input into the session.
4. CUT-174 plan card and Autopilot.
5. CUT-175 artifact canvas, CUT-176 Overview digest and timeline, CUT-179 phone.
6. CUT-177 Codex, then CUT-178 kopicode.

## Open (the first, third and fourth were decided in CUT-174: see ADR-0044)

- Whether the Autopilot supervisor is a satay workflow (ADR-0001, durable across a daemon restart) or a daemon loop whose
  state lives in the episodic journal. ADR-0032 left the same question open for a session-first path; the first slice
  that builds it should decide and update ADR-0044.
- ADR-0004 (no parallel transcript): the thread is read from the tool's own record plus journal events, not stored a
  second time. Whether the journal should also keep the agent's messages (so history survives a deleted transcript) is
  unsettled.
- Which permission mode a builder starts in under Autopilot, and what "stuck" means for an interactive session.
- Whether a Shell should ever get the project's secrets (an explicit toggle that lists the names first).
