# Session control spike (CUT-91)

Hands-on checks behind ADR-0032. Checks 1, 2 and 3 are done; check 4 (the Codex desktop app) needs the app and is open.

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

## Check 4

Not run. It needs the Codex desktop app (approval cards and background threads).
