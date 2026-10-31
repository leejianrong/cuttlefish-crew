# ADR-0052: The manager is an ordinary project, launched with the manager's MCP tools

- Status: Accepted (built, CUT-137)
- Date: 2026-11-01
- Deciders: development agent for CUT-137, open to review by Jian

## Context

ADR-0046 makes the manager an agent session, not a cuttlefish model call (ADR-0042), and ADR-0050 gave it a credential and
tools. Nothing launched it. It has to survive a daemon restart, be messageable from the dashboard, and work only through
those tools.

## Decision

1. **A project named `manager`, root `~/.cuttlefish/manager`.** No new entity: the tmux session, its resume, the chat and
   the terminal come with any project. `cuttlefish serve --manager claude-code|codex` makes it (or changes its backend) and
   starts it. `FleetDaemon.adopt_manager` finds it by root on later starts, sets `manager_project` (so ADR-0050's
   self-exclusion applies) and the existing reconcile restores it. The flag is not needed again.
2. **The launch is the only special case**, in `start_agent_session`, so a restart's relaunch carries it too: Claude Code gets
   `--mcp-config`, `--allowedTools mcp__cuttlefish-manager` and `--disallowedTools Bash,Edit,Write,NotebookEdit`; Codex gets
   `-c mcp_servers...` and `--sandbox read-only`. Instructions are the root's `CLAUDE.md`/`AGENTS.md`. The MCP config holds
   no secret: `cuttlefish mcp --manager` reads `manager-token` itself and, with no `--base-url`, the daemon's address from
   `daemon.json`.
3. **kopicode is refused** with a message: it serves MCP but has no MCP client. Follow-up upstream.

## Consequences

- The manager's permission questions go through the same hooks as any Claude Code session; it is never answered by itself.
- Nothing wakes the manager yet: it acts when a person messages it. A wake on a timer reuses ADR-0051's schedule.
