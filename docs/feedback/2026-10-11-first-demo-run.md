# First hands-on run of `make demo` (2026-10-11)

Jian ran the local app with Codex in Standard mode. This file records the feedback as given and the
decisions that came out of the conversation. The decision itself is ADR-0032; the research behind it is
`docs/research/agent-session-ux-comparison.md`.

## Feedback

1. **Too many permission prompts.** The agent kept asking, and approving meant leaving the Overview tab for
   Needs you each time.
2. **The UI is not intuitive and the agent is hard to talk to.** After Codex did part of the job the round
   ended, and the next prompt restarted from scratch. (Cause, confirmed in code: every delegation calls
   `thread/start` and never `thread/resume`; steering lands only at a round boundary.)
3. **Recent activity** should show the newest message first, or sit newest-last and auto-scroll to the bottom.
4. **Too many bells and whistles while the basics don't work.** Study how Claude Code, Codex and their desktop
   apps are designed before building more.

## The bottom line Jian asked for

- A dashboard over 20, 50 or 100 projects. A project is a git folder on the PC, maybe with a GitHub or GitLab remote.
- Per project, **one agent is enough**. It is the same as running Claude Code, Codex or kopicode in a terminal
  in that folder, with a UI around it. Seeing the real terminal from the UI is wanted.
- What Cuttlefish adds on top:
  - **a. Secrets management.** Replace `.env` files; secrets have leaked into agent context before.
  - **b. Approve from the UI**, reachable from a phone away from home (for example approve a plan remotely).
  - **c. Context management.** Make the agent write a handover prompt, clear its context, start a fresh agent
    with the handover, so work can run for hours, days or weeks.
  - **d. Intern-like communication.** Visuals (like Claude artifacts, but backend-neutral), demos
    (recorded first, live with text to speech later), slides and documents.
- Long-term goal: orchestrate 20 agents from a phone while they run on a PC, talking by text, voice and video.

## Decisions (2026-10-11)

| # | Decision |
|---|---|
| 1 | **Session first.** One continuous agent session per project is the primary object. The multi-role team and round workflow become an optional advanced mode and get no new development effort for now. Context refresh with a handover prompt happens only when the context fills up. |
| 2 | **Terminal view is required**, and stays even after a structured chat view exists, so commands can be run remotely. |
| 3 | **Claude Code is the daily driver**; Codex and kopicode (and more later) stay supported. |
| 4 | **Approvals:** default is "ask only for risky things", with remembered per-project "always allow" rules. |
| 5 | **Durability:** an agent runs in tmux on the PC. After a reboot it is relaunched with the tool's own resume command. Satay-style workflow replay is not needed for a single session. |
| 6 | **Remote access:** a private network (Tailscale) first, a public relay later. Keep the daemon's HTTP API as the single interface. |
| 7 | **Artifacts:** the agent writes files to a known folder (`.cuttlefish/artifacts/`) and the dashboard renders them. A tool call (MCP `present`) is added later, for plan approval. |
| 8 | **Spike first:** the comparison doc is done; four hands-on checks follow (see the research doc, "Open questions"). |

## Quick wins to do first

- Recent activity newest-last with auto-scroll that pauses when the reader scrolls up.
- Approve and decline inside the project view, no tab switch.
- A remembered "always allow" choice on every approval, per project.
