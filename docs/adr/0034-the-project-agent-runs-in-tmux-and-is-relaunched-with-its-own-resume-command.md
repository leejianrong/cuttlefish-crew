# ADR-0034: The project's agent runs in tmux and is relaunched with its own resume command

- Status: Accepted
- Date: 2026-10-16
- Deciders: Jian

## Context

ADR-0032 makes one long-lived agent session per project the primary object, and says it runs in tmux and comes
back after a reboot with its tool's resume command. The spike (`docs/research/session-control-spike.md`) showed the
recipe for Claude Code: a session started in tmux can be driven with `tmux send-keys`, and after the tmux session is
killed, `claude --resume <sessionId>` in a new one recalls the conversation.

## Decision

**Each project has at most one tmux session, on a socket of cuttlefish's own, running the backend's interactive tool
in the project root.** `SessionManager.ensure` starts it, reattaches to it or relaunches it, and records which in
`<root>/.cuttlefish/agent-session.json`.

- **Started:** nothing to resume. Claude Code is given `--session-id <uuid>` up front, so its id is known without
  scraping anything.
- **Reattached:** the tmux session was still there. The daemon restarted or the browser closed; nothing is touched.
- **Relaunched:** tmux was gone (a reboot, a kill). Claude Code: `claude --resume <id>`, falling back to a fresh
  `--session-id` if there is no conversation yet. Codex: `codex resume --last` (it lists by directory). kopicode:
  `kopicode -resume <newest id from kopicode sessions -json>`.
- The tmux server is a separate process from the daemon, so killing the daemon leaves every agent running. At
  `cuttlefish serve` start the daemon reconciles each project whose record says `wanted`, and prints which of the
  three happened. Stop clears `wanted`, so a deliberate stop is never undone.
- The socket is `~/.cuttlefish/tmux.sock` (a scratch `HOME` gets its own server), or a short hashed path under
  `/tmp` when `HOME` is too deep for a unix socket.
- The agent gets the allowlisted environment of ADR-0029 (`env -i` plus `merge_env`), never the daemon's.
- Reached through `GET /api/projects/{id}/agent-session` and `POST .../start` and `.../stop`, the Overview's
  **Agent session** card, and the MCP tools `get_agent_session`, `start_agent_session` and `stop_agent_session`.

## Consequences

- A tmux-hosted agent asks for permission **in its own terminal**. The terminal view (CUT-96) is where a person
  answers. *Update (ADR-0040, CUT-127):* a Claude Code session also raises a Needs you card through a held
  `PermissionRequest` hook; Codex and kopicode sessions still ask in the terminal only.
- A project's team workflow and its tmux session are separate: a stream-json round and the TUI must not both
  drive one Claude conversation at once. Which one a project uses is the person's choice for now.
- tmux must be installed. Without it Start says so; nothing else in cuttlefish needs it.
- Codex's `resume --last` takes the newest session for the directory, which is right for one agent per project and
  wrong if a person also uses Codex there by hand.
