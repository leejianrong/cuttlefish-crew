# ADR-0032: One continuous agent session per project is the primary object

- Status: Accepted
- Date: 2026-10-11
- Deciders: Jian

## Context

A hands-on run (`docs/feedback/2026-10-11-first-demo-run.md`) showed the product is shaped around a workflow
of rounds, not a conversation. Each delegation starts a new agent thread, so a follow-up prompt starts from
scratch; steering lands only at a round boundary (ADR-0008); approvals live on a separate tab. The vendors
already offer long-lived sessions, background sessions and phone access for a single agent
(`docs/research/agent-session-ux-comparison.md`).

## Decision

**A project has one long-lived agent session, and the dashboard is a view onto it.** The session is the
primary object, with a chat-like thread (messages, tool calls, approvals inline, replies) and a raw terminal
view of the same session. Cuttlefish lists, attaches to and watches each tool's own long-lived session
(`claude --bg`/`attach`, the Codex app-server daemon with `thread/resume` and `queue`, `kopicode serve`)
instead of relaunching it per round. A fresh agent with a handover prompt starts only when the context fills.

Following from it:

- The agent runs in tmux on the PC; after a reboot it is relaunched with its tool's resume command.
- Approvals default to "ask only for risky things", with per-project always-allow rules.
- Agents show visuals by writing files to `.cuttlefish/artifacts/`; no new protocol (ADR-0003, ADR-0005).
- Remote access starts on a private network (ADR-0013); a public relay comes later.
- The team and round workflow (ADR-0001, ADR-0007) stays, as an optional advanced mode, with no new work.

## Consequences

- ADR-0001 still holds for the paths that use the workflow; a session-first path does not have to be a satay workflow.
  Whether it should be is open, and the handover and episodic-journal rules (ADR-0004) still apply to it.
- Backends become longer-lived and stateful, which changes the lifecycle code in `agents/` and the daemon.
- Four hands-on checks gate the Claude and Codex work (the research doc's open questions).
