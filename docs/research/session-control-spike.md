# Session control spike (CUT-91)

Hands-on checks behind ADR-0032. Checks 1, 2, 3 and 5 and the kopicode section are done; check 4 (the Codex desktop app) needs the app and a person, and is open.

## Check 2: can two clients drive one Codex thread? (done, 2026-10-16)

Yes, on the `ws://` transport of a plain `codex app-server --listen ws://127.0.0.1:PORT`.

- `codex-cli 0.155.1`, ChatGPT login, tiny turns, one run. A 60-line Python client opened two websocket
  connections (A and B) to the one server. Both sent `initialize` then `initialized`.
- A did `thread/start` and a turn. B then did `thread/resume` on the same thread id while A was still connected.
  It succeeded, and B received that thread's notifications from then on.
- B started a turn that ran `sleep 20`. A received the same `thread/status/changed`, `turn/started`, `item/*`,
  `item/agentMessage/delta`, `thread/tokenUsage/updated` and `turn/completed` stream as B (39 and 41 events).
- Six seconds into B's turn, A called `turn/steer` with B's turn id as `expectedTurnId`. It was accepted and returned
  that turn id. A second run (`approvalPolicy: untrusted`) confirmed both: the final answer was "PELICAN STEERED", so the
  steer text landed and the context from the first client's turn survived B's `thread/resume`.
- Status events are broadcast, not per client: a `thread/status/changed` to `active` reached both clients when either
  started a turn.

What it means for CUT-92:

- The dashboard can hold a long-lived app-server and a thread per project, and call `thread/resume` then `turn/start`
  for each prompt, instead of `thread/start` every round. A person's TUI (`codex --remote ws://...`) can sit on
  the same thread.
- `turn/steer` gives mid-turn steering without waiting for a round boundary, which ADR-0008 had to accept.
- Approvals on a shared thread: **every subscribed client receives the same `item/commandExecution/requestApproval`**,
  and then a `serverRequest/resolved` notification. Both clients answered at once in the test and nothing broke,
  but which answer counted was not recorded. The dashboard must treat the first answer as final, and drop its own
  card when `serverRequest/resolved` arrives (a person answering in the TUI resolves the dashboard's card too).
- The `ws://` listener binds to loopback only. A phone needs the private network in front of it (ADR-0013), not a
  public bind.

## Check 1: does `claude --bg` give structured events? (done, 2026-10-16)

Not on its own surface, but the transcript on disk is structured. `claude` 2.1.300, one small run.

- `claude --bg "<prompt>"` prints a short id (`8c7a3fd0`) and runs the session in a hidden terminal.
- `claude logs <id>` prints **the terminal's escape-code stream**, a replay of the screen (cursor moves, colours, the
  spinner). It is not events. Parsing it would mean screen scraping.
- `claude agents --json [--cwd <dir>] [--all]` lists sessions as JSON: `pid`, `id`, `cwd`, `kind`
  (`background` or `interactive`), `sessionId`, `name`, `status` (`idle`), `state` (`done`). No events, no pending
  approval.
- The session's transcript, `~/.claude/projects/<cwd with / as ->/<sessionId>.jsonl`, is NDJSON with `user`,
  `assistant` (content blocks `tool_use`, `text`) and `tool_result` entries, plus `system` and `attachment` lines.
  A dashboard can tail it for a chat view, read-only. An approval prompt is not in it, because it lives in the TUI.
- `claude stop <id>` and `claude rm <id>` cleaned the session up.

For CUT-93: a chat view of a Claude session comes from tailing the transcript. Answering approvals and sending
prompts needs the live process (the stream-json transport we already use, or tmux keys), not `--bg`.

## Check 3: Remote Control for a Claude session started in tmux (done, 2026-10-16)

Yes, and the URL is readable.

- `tmux new-session -d -s s -c <dir> "claude --remote-control <name>"` started an interactive session that
  registered at once. The banner line is `/remote-control is active · Continue here, on your phone, or at
  https://claude.ai/code/session_...`, and `tmux capture-pane -p` returns it. `claude agents --json` lists the
  session (`kind: interactive`) but does not carry the URL, so the pane is the only source found.
- `tmux send-keys -t s "<prompt>" Enter` drove the session, and the reply and transcript updated.
- After `tmux kill-session`, `claude --remote-control <name> --resume <sessionId>` in a new tmux session recalled
  the earlier turn and printed **the same URL**. So the URL belongs to the conversation, not the process.
- The session runs in auto mode by default here (`auto mode on` in the footer).

For CUT-95 and CUT-98: the reboot path is "tmux, then `claude --resume <sessionId>`", with the session id taken
from `claude agents --json` (or the transcript's file name) and stored per project. A phone can use Anthropic's own
Remote Control URL for a single session, as the research doc said. Cuttlefish does not need to rebuild that for
Claude, only to show the link and list the sessions.

## Check 5: can a tmux-hosted Claude Code session raise a Needs you card? (done, 2026-10-17, CUT-126)

Yes, with a `PermissionRequest` hook. `claude` 2.1.301, an interactive session in tmux, `--permission-mode default`, tiny
prompts on the Haiku model (a few cents). The scripts are in `claude-hook-spike/`. Each line below was observed, not read
from the docs; where the docs say something the checks did not reproduce, it is marked.

**What fires, and when** (permission mode `default`, a `mkdir` that needs approval):

- `UserPromptSubmit`, then `PreToolUse` (0.1 s before the next event), then **`PermissionRequest` at the moment the dialog
  appears**. With no decision the normal dialog shows.
- **`Notification` with `notification_type: permission_prompt` arrives 6.0 s later** and says only "Claude needs your
  permission", with no tool or command. Too late and too thin to be the main signal.
- Payload fields. `PermissionRequest`: `tool_name`, `tool_input` (the command and its description), `permission_mode`,
  `session_id`, `transcript_path`, `cwd`, `prompt_id`, `permission_suggestions`. It has no `tool_use_id`, but the
  `PreToolUse` just before it does, with the same input.
- **Auto mode never asks.** The same `mkdir` in this install's default (`auto mode on`) ran with no dialog and no
  `PermissionRequest`. A project's approvals only exist if the session is launched with a mode that prompts.

**The hook can answer for the person** (the dialog stays on screen while it waits):

- A hook that holds 3 s and returns `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}`:
  the dialog was visible for the whole wait, vanished when the hook answered, and the command ran (`PostToolUse` followed).
- `behavior: "deny"` with a `message`: no dialog, the command did not run, and the turn ended (`Stop`).
- So a held hook plus a daemon endpoint is a real Allow and Deny on a phone, with the terminal as a second place to answer.

**Failure never approves:**

- A hook that exits 1, or exits 2, or prints nothing: the dialog stays and the command waits for the person.
- A `timeout` (seconds, on the hook entry in settings) kills a hung hook with SIGTERM when it expires. The dialog stays and
  nothing runs. The docs give 600 s as the default, which was not tested.

**Answering in the terminal while the hook is still held:**

- The command runs (or not) as typed. **The hook process is not cancelled and gets no signal.** It stays alive until its own
  timeout. A hook that holds for the daemon must therefore watch for the item having been resolved elsewhere and exit.

**How a waiting item is cleared:**

- Allowed in the terminal: `PostToolUse` fires. Then `Stop` at the end of the turn.
- **Denied in the terminal: no hook event fires at all** (no `PostToolUse`, no `PostToolUseFailure`, no `Stop`). The one
  structured signal is the session transcript: a `tool_result` with `is_error: true`, the text "The user doesn't want to
  proceed with this tool use", the `tool_use_id`, then "[Request interrupted by user for tool use]". The daemon can tail it
  (check 1 already established the transcript is NDJSON) and match the denied call by `tool_use_id`, learned from the
  `PreToolUse` hook, or by its input. The next `UserPromptSubmit` also clears everything for that session.

**Getting hooks in without touching the project:**

- `claude --settings <file>` loaded our hooks and left the project's files alone. A project's own `.claude/settings.json`
  hook for the same event also ran, once each. They merge; neither replaces the other.
- A new folder shows "Do you trust this folder?" before anything else. It appears in the terminal and the Terminal tab
  can answer it, but the daemon cannot assume the session is ready until it passes.

**Recommendation.** Build level 2: a held `PermissionRequest` hook that raises a Needs you card with Allow and Deny
(`RequestRaised` and `RequestResolved`, ADR-0028), and on any failure does nothing, so the terminal dialog is the fallback.
Level 1 (a "waiting in terminal" item) is the same hook without the decision, so it comes free. Requirements it must meet:

- The never-allowed list holds in every mode, so the hook denies those commands without asking (V4), and `tool_input`
  shown on a card goes through the same redaction as the journal.
- Launch with `--permission-mode` taken from the project's mode (ask first and standard map to `default`, auto to
  `auto`); a project left on auto raises nothing, and the card for such a project should say so.
- A hook `timeout` below the daemon's own deadline, and an exit that is never an allow.
- Clearing: the held hook polls the daemon; the daemon closes the item on `PostToolUse`, on the transcript's rejected
  `tool_result`, on `Stop`, on `UserPromptSubmit` and on a session that is gone.

Not checked: Codex in tmux (not looked at for a hook; kopicode is in the next section), a hook on Claude Code's other
permission modes (`acceptEdits`, `plan`), `permission_suggestions` for an "always allow" button, several sessions at once,
and the 600 s default.

## kopicode: serve and the REPL (done, 2026-10-18, CUT-91)

kopicode `v0.5.1-0.20261017122613-5b8f4af73904` (main, v0.5.0 plus 14 commits), built with `go build` into a scratch directory.
The kopicode checkout's `bin/kopicode` and `~/.local/bin/kopicode` were both **v0.4.0**, older than the release, so they were not
used. Model `deepseek/deepseek-v3.2`, tiny prompts, a scratch git repo, a scratch `HOME`. The key was sourced inside the child's
own shell and never read. About half a cent in all (the serve run's provider-reported `cost_usd` was 0.0034).

### Can a second client attach to a kopicode session? Partly: it takes over, it does not share

`kopicode serve --listen <sock> --consent-timeout 5m`, a 60-line Python client with two connections to the one socket.

- Client A sent `session.start` (`consent_mode: remote_interactive`, `ask_mode: remote`, a prompt asking for `echo hello-c91`)
  and received `consent.request` `c-1`, which it did not answer. `server.sessions` on A showed `state: awaiting_consent`,
  `pending_request: "c-1"`, `turn`, `usage` (with `cost_usd`) and `last_event_at`.
- Client B connected. **The server closed A at once, with no message** (A's next write failed with a broken pipe). That is the
  documented "one client at a time" rule, so two clients never watch one session together, unlike the Codex `ws://` check.
- B received the pending `consent.request` again **under the same id** (`c-1`). `server.sessions` on B showed the same
  `awaiting_consent` row. `session.events` with `after_seq: 0` returned all 8 events A had seen (`seq` 1 to 8,
  `session_started` to `tool_call_parsed`), `last_seq: 8`, `more: false`. So a client that reconnects loses nothing.
- B answered `{"answer":"allow"}` to `c-1`. The turn went on, and B received the live `session.event` notifications (`seq` 9 to
  17: `permission_requested`, `permission_decided`, `tool_result`, `turn_snapshot`, `verification`, the next provider call,
  `assistant_message`). A got nothing, being closed. The turn's own result, the response to A's `session.start`, went to
  nobody (the documented rule: a response stays with its connection). B reads the outcome from `server.sessions`:
  `state: idle`, `last_stop: completed`.
- B then sent `session.submit` with a prompt that makes the model call `ask`. It received `ask.request` `c-2`, answered
  `{"text":"teal"}`, and got the turn result (`stop: completed`, `usage` with `cache_read`, `cost_usd`). `session.usage` answers
  at any time. `server.shutdown` ended the process with exit 0 and a `session_ended` event.

What it means for a dashboard:

- The first client's pending approval is **not** resolved by the second client attaching. It is re-asked of whoever is connected
  now, so a dashboard that reconnects can answer approvals its own earlier connection left open. A person's second tool cannot
  watch in parallel; a supervisor and a viewer must share one connection (cuttlefish already owns the one).
- There is **no mid-turn steer**: `session.submit` queues behind a running turn, and `session.cancel` is the only interrupt.
  Steering lands at the turn boundary, as ADR-0008 accepted. Codex's `turn/steer` has no kopicode twin.
- Nothing tells the old client it was replaced. A client that reconnects after a gap should call `server.sessions` and
  `session.events` before it assumes anything.
- The pending question is visible in `server.sessions` (`pending_request`) and in the notification stream (`consent.request`
  and `ask.request` are requests, not events), but **not in the journal**: `permission_requested` is written only once an answer
  exists (seq 9 after the answer, seq 8 before it). Replay therefore cannot show an approval that is still open.

### The REPL in tmux

`tmux -S <own socket> new-session -d -c <repo> "env -i PATH=... HOME=<scratch> bash -c 'set -a; . <key file>; set +a; exec kopicode --model deepseek/deepseek-v3.2'"`.

- It asks in the terminal. `tmux send-keys "<prompt>" Enter` started a turn; the pane then showed
  `[perm] run_shell needs your consent`, `command: /bin/sh -c echo hello-repl` and `allow? [y]es / [N]o / [a]lways ...`.
  `tmux capture-pane -p` returned that text, so the pane is readable. `send-keys y Enter` answered it, the command ran, and the
  pane showed `[perm] allow (user)`, `[tool] run_shell: ok, exit 0`, `[snap] refs/kopicode/<session>/1`, `Done.`
- There is **no hook or structured signal** for a pending approval. Kopicode's hooks (ADR-0036) are `pre_tool`, `post_tool` and
  `stop`, set in the user config, and the REPL runs them; none fires for a consent prompt. The only signals are the pane text
  (the `[perm]` and `allow?` lines) and a quiet journal. Matching the pane is screen scraping, as `claude logs` was.
- The record on disk is structured and tail-able: `<repo>/.kopicode/sessions/<id>/events.jsonl`, one JSON object per line with
  `schema_version`, `session_id`, `seq`, `turn`, `type` (`ToolCallParsed`, `PermissionRequested`, `PermissionDecided`,
  `ToolResult`, `TurnSnapshot`, `VerificationRun`, `AssistantMessage`, `SessionEnded`, ...), `ts` and `payload`. While the
  prompt was open the file ended at `ToolCallParsed` (seq 9). `PermissionRequested` and `PermissionDecided` (seq 10, 11) appeared
  after the answer. `/exit` wrote `SessionEnded`.
- The REPL printed `[retry] attempt 1, try 1 of 6: http 429` twice (the provider rate-limited the cheap model), and the retries are
  in the journal as `ProviderRetried`.

For cuttlefish: a person or the dashboard driving a kopicode REPL in tmux can answer approvals with keys and read the pane, but
cannot be told one is waiting, except by the journal going quiet after `ToolCallParsed`. The reliable path for approvals is
`serve` with `remote_interactive` (what V4-H already uses), not tmux. If a kopicode terminal session ever needs a Needs you card,
it needs a kopicode change: an event written when the request is raised, or a `consent` hook.

Not checked: several `serve` sessions answering approvals at once, a client that never answers (the 60 s default denial is
documented, not run), and `serve` over stdio with a replaced client (there is no replacement over stdio).

## Check 4

Not run, and it cannot be run from this machine. It needs the Codex desktop app (approval cards and background threads), which
is a GUI on a logged-in desktop and not reachable from the WSL shell used for the other checks. What remains: open the app on a
repo whose thread a `codex app-server` is also driving, start a command that needs approval, and record whether the app raises
an approval card, whether it shows a thread started by another client, and whether answering in the app resolves the same
`item/commandExecution/requestApproval` the server sent (`serverRequest/resolved`). A person has to do it.
