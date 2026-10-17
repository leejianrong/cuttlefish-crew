# ADR-0040: A Claude Code session in tmux raises Needs you cards through a held permission hook

- Status: Accepted
- Date: 2026-10-17
- Deciders: Jian (CUT-127, which asked for this ADR first); the choices below are open to review

## Context

A Claude Code agent in tmux (ADR-0034) asks for permission in its own terminal, so it never reaches Needs you or a phone,
and the count under-reports the main path (ADR-0032). The spike (`docs/research/session-control-spike.md`, check 5,
CUT-126, `claude` 2.1.301) found that a `PermissionRequest` hook fires at the moment the dialog appears, can hold while
the dialog stays on screen and answer allow or deny for the person, and never approves on a crash, a non-zero exit, no
output or a timeout. Answering in the terminal does not cancel a held hook. An allow in the terminal fires
`PostToolUse`; a deny fires nothing, and only the session transcript records it. Auto mode never prompts.

## Decision

**The tmux session loads a settings file of ours that adds hooks, and the held hook raises a request exactly as a
kopicode or stream-json request does (ADR-0028).** Nothing about the project's own files changes.

### Launch

- `claude --settings <root>/.cuttlefish/claude-settings.json` (the file is rewritten at each launch; a project's own
  `.claude/settings.json` hooks still run, the two merge) and `--permission-mode manual` for Ask first and Standard,
  `--permission-mode auto` for Auto. The mode is read when the session starts, so changing a project's mode applies to the
  **next** session start, and the Agent session card says so.
- Hooks: `PermissionRequest` (held), `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop`. Each runs the same small
  stdlib-only script (`sessions/hook_script.py`) with `--project <id>`.

### The script and the daemon

- The script POSTs the hook's JSON to `POST /api/hooks/claude-code/{project_id}` on the running daemon and prints what it
  is told. It finds the daemon through `~/.cuttlefish/daemon.json` (url and pid, written once the daemon is listening and
  removed when it exits) and proves itself with `~/.cuttlefish/hook-token` (mode 0600, kept across restarts like the
  loopback token), so the daemon can restart on another port and a running session carries on.
- **Anything wrong prints nothing and exits 0**: no daemon, a bad token, a timeout, an error. Claude Code then shows its own
  dialog, which stays the fallback. The script never prints `allow` unless the daemon said so in this response.
- The endpoint has its own token and no tier; the owner's token and a device's tier do not apply to it, and the hook token
  opens nothing else. It can raise a card and nothing more: it cannot answer one. At most 20 cards wait per project.
- Timeouts nest: the daemon's request window (`CUTTLEFISH_REQUEST_WINDOW`, 10 minutes by default) ends first and answers
  "no decision", the script's socket timeout is five seconds longer, and the settings `timeout` fifteen seconds longer than
  the window, so a hook always exits by itself.

### What is decided, and what is asked

Mirroring the stream-json transport (`delegate/claude_code_live.py`) and ADR-0025:

- **Bash**: the project's own `ConsentPolicy` (the same `command_decider` the other backends use). On the allow list: allow,
  no card. Never-allowed (`sudo`, `rm -rf` outside the root, `git push --force`, `curl ... | sh`): deny at once with a
  reason, no card, in every mode. Off the list, or a shell-syntax line: a Needs you card with the command (Allow once,
  Allow always where a rule can be suggested, Deny). Ask first has no list, so every command is a card.
- **Edits** (`Edit`, `Write`, `MultiEdit`, `NotebookEdit`): inside the project folder, allow; outside it, deny at once.
- **`AskUserQuestion`**: no decision; the terminal asks. A question card for tmux is a later slice.
- **Any other tool** (web, a task, an MCP tool, leaving plan mode): a card with Allow once and Deny.
- **Auto**: the session never prompts, so no card is raised, and the card on the Overview says so.
- `tool_input` shown on a card is journaled through the same write-time redaction as everything else (ADR-0004), and a card
  shows at most 1 KiB of it.

### Where it is journaled, and how a card ends

- The requests belong to the project's **agent session**, not to a team: scope id `session:<project id>`, in the project's
  own episodic store, so history and the sweep after a restart read it like a team's. One session per project (ADR-0032).
- An answer from the dashboard returns allow or deny to the held hook. The card ends `superseded` (answered in the
  terminal) when the terminal answered first, found by: `PostToolUse` (allowed there), the transcript's rejected
  `tool_result` for the call (denied there; the call's id is learned from the `PreToolUse` just before), the next
  `UserPromptSubmit`, or `Stop`. It ends `expired` when the window passes, `abandoned` when the hook process went away,
  and `cancelled` when the person stops the agent session.
- The daemon restarting abandons every unresolved card, as ADR-0028 already does for a team. **Shutting down does it
  first**: uvicorn waits for in-flight requests, and a held hook is a request that waits for a person, so without this
  Ctrl-C hung for the whole request window with a card pending (found in the browser run). The server releases every
  held hook as shutdown begins; each script gets "no decision" and exits, and the terminal's dialog stays.

## Consequences

- A Claude Code agent in tmux shows in Needs you, on the badge and on a phone, and can be allowed or denied from there. The
  terminal still works, and either place can answer.
- A session started before this ships, or one whose project switched to Auto, raises nothing; relaunching it fixes the first.
- It depends on Claude Code's hook payload and the transcript's wording for a rejected call. Both were observed, not
  documented; if they change, a card for a call denied in the terminal lingers until the next prompt or `Stop`, and it
  never approves anything.
- Codex and kopicode sessions in tmux are unchanged and still ask in their own terminals.

## Alternatives considered

| Option | Why not |
|---|---|
| `--permission-prompt-tool` or stream-json for the tmux session | That replaces the interactive TUI the Terminal tab shows; ADR-0032 wants the real terminal kept. |
| A `Notification` hook alone | It arrives six seconds late with no command, so it can raise a vague card and answer nothing. |
| Writing the project's `.claude/settings.json` | It edits a file the project owns and may commit. `--settings` leaves it alone. |
| The hook holding the daemon's owner token | One token that can do anything, readable by every command the agent runs. The hook token can only raise a card. |
| An unrestricted allow when the daemon is unreachable | A hook that approves on failure approves exactly when nobody is watching. Failure prints nothing. |

## Checked

Integration tests (`tests/integration/test_claude_hooks.py`) call the hook route as the script does, against a real daemon,
broker and journal, with a person answering through the dashboard's own routes: an off-list command is a card and Allow
once and Deny reach the held hook; Always allow saves the rule; a command on the list runs and a never-allowed one is
refused with no card; edits inside and outside the folder; Auto and a question left to the terminal; any other tool as an
Allow-or-Deny card; redaction of a secret on the card; `PostToolUse`, the next prompt and `Stop` clearing a card; a refusal
found in a growing transcript (and a refusal of a different call ignored); expiry, a hook that went away, stopping the
session, the 20-card cap, a restart's sweep, who may call the route, and shutdown releasing held hooks. The hook script is
run as a subprocess against a stub daemon: it prints a decision only when told, and prints nothing for no daemon, a bad
token, an error status, garbage, a slow daemon and a non-permission event. Five properties were broken on purpose (a
never-allowed command asked about, the script printing allow for any event, no token on the route, edits outside the folder
allowed, an expired card answered allow), and each made tests fail. Chromium at 390px against a real daemon and a fake
`claude` that loads `--settings` and calls the hooks the way Claude Code does, including answering in the terminal while
the hook is still held: the card appears with the count and title, Allow once ran the command, a never-allowed command was
refused with no card, an answer in the terminal cleared the card, Deny from the dashboard refused, a refusal in the
terminal cleared the card through the transcript, and a project switched to Auto launched with `--permission-mode auto`
and raised nothing. The real `claude` 2.1.302 accepts `--settings <our file> --permission-mode manual|auto` and starts.
**Not run:** a real prompt through the real `claude` with these hooks (it costs model credit; the spike on 2.1.301 saw the
hook events and the transcript wording this relies on), Codex and kopicode sessions, and Safari or Firefox.
