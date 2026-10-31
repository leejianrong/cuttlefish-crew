# ADR-0047: A project can run any terminal command in its tmux session, with no status integration

- Status: Accepted (built, CUT-233)
- Date: 2026-10-31
- Deciders: Jian (card CUT-233), development agent

## Context

The agent session (ADR-0034) knows three tools, each with its own resume command and, for some, a record cuttlefish
reads (chat, Needs you through hooks, ADR-0040). herdr runs any terminal agent
(`docs/research/fleet-manager-landscape.md`). Supporting a fourth tool the same way needs a reader per tool, which
ADR-0003 and ADR-0005 forbid inventing a shared format for. What a person wants first is that the tool stays up in
tmux and the Terminal tab (ADR-0035) attaches to it.

## Decision

1. **One nullable column on the project, `terminal_command`.** When set, the project's agent session runs
   `sh -c <command>` in the project root, on the same tmux socket and session name, with the agents' filtered
   environment (ADR-0029) and no model keys (`agent_env` has none for it). It overrides the backend for the
   session, not for rounds and teams, which still use the project's backend.
2. **The record says `backend: "terminal"`, `asks: "none"`.** Everything that reads the session by backend already
   falls through for an unknown name: chat reports `supported: false`, typing from chat is refused, no hooks file is
   written, Autopilot has no transcript.
3. **No resume, no first message.** A session that exits is gone; the next start runs the command again, and the card
   says "from the start", never "the conversation carries on". A goal is refused with a plain message.
4. **Admin to set it.** It is an arbitrary shell command, so `PATCH .../terminal-command` is Admin tier, like presets.
   `projects add --terminal-command` is the CLI way; there is no dashboard form yet.
5. **The page says it has no status** (AGENTS.md: never imply a live prompt where there is none): the Agent session
   card, the Overview's goal card and the Terminal tab all say there is no live status, chat, goal or Needs you
   cards for it, and that whatever it asks appears only in the Terminal tab.

## Consequences

- Any CLI agent works on day one with no code per tool; none gets chat, Needs you, resume or Autopilot until it has its
  own ADR.
- The never-allowed list does not apply: cuttlefish sees none of what the tool runs. That is stated, not hidden.
- A command that needs a key gets it only through `CUTTLEFISH_AGENT_ENV_PASSTHROUGH`.
