# Agent session UX comparison (spike, 2026-10-11)

Research only, no product code. The question: if cuttlefish-crew's centre becomes **one continuous agent
session per project, watchable and steerable from a phone**, what do Claude Code (CLI and desktop app), Codex
(CLI and app) and kopicode already give us for session model, approval UX and resume behaviour?

## Method and limits

- Local: `claude` 2.1.296, `codex-cli` 0.155.1, `kopicode` v0.4.0. Read `--help` and subcommand help only. No live
  sessions were run for this spike.
- Web: Anthropic's own docs for the Claude Desktop app and permission modes (`code.claude.com/docs/en/desktop`,
  `.../permission-modes`). The Codex desktop app and Claude Remote Control were checked against third-party
  pages only, so those rows are marked *(secondary)* and need confirming before we build on them.
- Earlier spikes already cover the wire protocols: `codex-app-server-spike.md`, `claude-code-stream-json-spike.md`.

## Session model

| | Claude Code | Codex | kopicode |
|---|---|---|---|
| Unit of work | A **session**: its own history, folder and changes. Desktop lists many in a sidebar and runs them in parallel with git isolation. | A **thread**. The desktop app shows several threads per project, running in parallel *(secondary)*. | A session per working tree (one `--root` lock). |
| Runs without a terminal attached | `claude --bg` starts a background session and prints an id. `claude agents` lists them, `attach`, `logs`, `stop`, `respawn`, `rm` manage them. | `codex app-server daemon` is a shared local daemon. `codex agents` browses every session on it. `codex remote-control start/pair` exposes it. | `kopicode serve` holds a process open and drives sessions over JSON-RPC on stdio. |
| Add a message to a live session | Attach, or type in the remote client. | `codex queue --thread <id> --message <text>` queues a message into an existing session. | `serve` RPC. |
| See it in a terminal | Native TUI. Desktop has an integrated terminal pane that shares the session's working directory. | Native TUI, and the TUI can connect to a remote app server (`--remote ws://...`, bearer token from an env var). | Native REPL. |

## Approval UX

| | Claude Code | Codex | kopicode |
|---|---|---|---|
| Modes | `default` (reads only run free), `acceptEdits`, `plan`, `auto` (a classifier model reviews actions instead of you), `bypassPermissions`. Selector sits beside the send button. | `approvalPolicy`: `untrusted`, `on-request` (the default for local work), `never`; sandbox: `read-only`, `workspace-write`, `danger-full-access`. | Consent policy per command, in-root edits implicit. |
| Starting mode | `auto` is the built-in start mode for terminal and VS Code from v2.1.283. A mode picked in Desktop is remembered **per folder**. | Config file. | Flags. |
| "Always allow" | Desktop's card offers Allow once, Always allow, Deny. Rules layer on top of modes, and deny rules win in every mode. | `acceptForSession` and an exec-policy amendment ("always allow this command prefix"). | Always allow, saved project-wide in our implementation. |
| Never auto-approved | Explicit ask rules, `rm` on critical paths, protected paths. Holds even in bypass mode. | Not documented. | Our own never-allowed list. |

The point for us: **Claude Code's default for a terminal user is a classifier, not a person.** Asking
a human for every command is the Standard-mode behaviour that annoyed you, and Claude Code itself moved away from it.

## Resume behaviour

| | Claude Code | Codex | kopicode |
|---|---|---|---|
| Continue the last session | `claude -c`, `claude --resume <id>`, `--fork-session` to branch | `codex resume [id\|--last]`, `codex fork` | `--resume`, `--fork`, `kopicode sessions` |
| Survives the process dying | Yes, the conversation is on disk. A stopped background session keeps its conversation and `attach` reopens it. | Yes. `thread/resume` in a new process kept context (our app-server spike). | Yes, ids from `kopicode sessions`. |
| Move between machines | Desktop can send a session to the cloud as a **summary**, not the full history. | Codex Cloud tasks. | None. |
| What Cuttlefish does today | Starts one process **per round** and one new thread per delegation. That is the "restarts from scratch" you saw. | `thread/start` every round, never `thread/resume` in the product path. | Per-round `serve` session. |

## Remote access, found while checking

- **Claude Code Remote Control** (`claude --remote-control`, `/remote-control`): the local session dials out over
  HTTPS and registers with Anthropic, so no inbound port is opened. It then shows up at claude.ai/code and in the
  Claude mobile app, with a URL and QR code. One remote session per Claude Code instance *(secondary, official page
  `code.claude.com/docs/en/remote-control` not read in full)*. This is the "public relay" option, run by Anthropic
  for Claude only.
- **Claude Desktop Dispatch**: message a task from your phone, it can spawn a Code session, and you get a push when
  it finishes or needs approval. Needs a Pro or Max plan.
- **Codex `remote-control`** (experimental): starts the app-server daemon with remote control on and prints a
  short-lived pairing code.
- **Desktop notifications** when a session finishes while you are not looking at it.

## What this changes

1. **Phone control of a single Claude Code session is a product Anthropic already ships.** We should not rebuild
   it for Claude. Cuttlefish earns its place on what none of the vendors do: **one dashboard across 20 to 100 projects
   and across vendors**, managed secrets, vendor-neutral artifacts, and automatic context handover.
2. **Cheapest honest path to "session per project":** use each tool's own long-lived session rather than a
   round loop. Claude: `claude --bg` plus `attach`/`logs`/`stop`. Codex: the shared app-server daemon plus
   `thread/resume` and `queue`. kopicode: `serve`. Cuttlefish becomes the thing that lists, attaches to and
   watches them, instead of the thing that re-launches them every turn.
3. **A pty and xterm.js in the browser remains the vendor-neutral terminal view**, and the only one that works for
   all three. Native attach is better where it exists (`claude attach`, `codex --remote`), so the terminal view can
   run those commands inside the pty.
4. **Approvals:** default to Claude Code's own lesson. Run auto or accept-edits-style modes and only interrupt a
   person for things on the risky list, with per-project saved rules. This matches your answer to question 4.
5. **Handover** stays ours. None of the three hand a fresh agent a summary when context fills, apart from
   Claude Desktop's cloud move, which summarises once.

## Open questions this spike could not answer

- Does `claude --bg` expose a structured event stream, or only terminal output via `logs`? If only text, the
  structured chat view for Claude needs stream-json and a second process.
- Can a second client (our dashboard) attach to a Codex thread that the Codex TUI is also driving?
- Does Remote Control work for a session that cuttlefish started in tmux, and can its URL be read programmatically?
- What does the Codex desktop app do for approval cards and background threads? Only secondary sources so far.

Each is a half-day of hands-on testing against a scratch repo, in the style of the earlier spikes.
