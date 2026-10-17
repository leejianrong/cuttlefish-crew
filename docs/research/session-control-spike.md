# Session control spike (CUT-91)

Hands-on checks behind ADR-0032. Checks 1, 2, 3 and 5 are done; check 4 (the Codex desktop app) needs the app and is open.

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

Not checked: Codex and kopicode in tmux (neither has been looked at for a hook), a hook on Claude Code's other permission
modes (`acceptEdits`, `plan`), `permission_suggestions` for an "always allow" button, several sessions at once, and the
600 s default.

## Check 4

Not run. It needs the Codex desktop app (approval cards and background threads).
