# Plan: an autonomous portfolio of agents you check in on, not supervise

Recorded 2026-10-31 from a planning discussion with Jian, after the landscape research in
`docs/research/fleet-manager-landscape.md`. Board: epics CUT-E21 to CUT-E28 on board 23. This is the product direction;
the ADRs that follow from it are written by the first card of each epic.

## What Jian wants

1. **No low-level approvals.** Agents run off and do the coding. The human is not asked about commands or edits.
2. **Scale: 20 to 100 projects at once, 1 to 5 agents each**, running for hours, days or weeks.
3. **Check-in is the product.** When the human opens a project they talk to its agents seamlessly and it is engaging: not
   only a wall of text, but slides, a demo of the app, and a call where the agent says out loud what it built.
4. **Copy Claude's artifacts** (slides, dashboards, wireframes, animations) so that *any* coding agent can generate and
   present them.
5. **Many worker machines** (PC, laptop, remote VM, cloud service) on one dashboard, viewable from any device.
6. **Outside apps:** Notion, Slack, Telegram, Google Meet, calendar, email and the like.
7. **Real dev tooling on any worker**, cloud ones especially: Claude's remote VMs make `docker compose` for development
   hard. Replicate developer and agentic tooling (skills, tools, docker, uv) on whichever machine the user picks.
8. **A front door that sells it.** Our landing page, install and first impression lag herdr, Paseo and Conductor.

## Why this is a different product from the field

The research found three kinds of product: local multiplexers (herdr, Paseo, Claude Squad), vendor cloud agents (Devin,
Cursor, Codex), and phone remotes. Every one of them is built around a human watching a session. Nobody builds for a human
who has 50 projects and looks at each for two minutes a day. That is the wedge, and it asks for three things the field
lacks: delegation without approvals, a report you want to open, and workers you choose and equip.

## What already exists (do not rebuild)

- The manager across projects, its spend cap, digests and media in updates: epic CUT-E18 (CUT-137, 138, 139, 150, 151).
- The canvas for images, video and sandboxed HTML (ADR-0036 update, CUT-175), block types (CUT-114), HTML on a second
  origin (CUT-115), the phone chat (CUT-179), the Overview digest (CUT-176).
- Autopilot, Needs you, never-allowed commands, permission modes; the credential broker.
- Relay, pairing, tiers, Tailscale https, the hosted relay (E16, E19); the project environment (ADR-0029, V5).
- Spike CUT-105 (a role presents through vikaki into a meeting page).

## Conflicts with decisions already made (each has a decision card)

| Existing decision | The vision | Handled by |
|---|---|---|
| Q69: voice and video are **deferred**; "do not recreate Zoom, Meet or Discord" | Agent hops on a call and speaks | **Decided 2026-10-31: still deferred, target March 2027**, after the core is cleaned up. CUT-E24 holds the work. Text-to-speech narration is not deferred: it ships earlier as an agent tool (CUT-201, epic E23) |
| CUT-140 / ADR-0042: cuttlefish makes **no model call of its own** | The manager, narration and voice all need model calls | **Decided 2026-10-31: no exception.** cuttlefish provides tools and the stage; the coding agent is the presenter, the speaker and the manager. A manager is a session on an AgentBackend (CUT-137, 151 now carry a note to that effect) |
| Needs you is the heart of the dashboard | Human is rarely asked | **Decided 2026-10-31:** remove most approvals, or delegate them to a manager agent (CUT-181 ADR, CUT-230). The never-allowed list holds in every mode and for the manager |
| Secrets stay on the machine; the hosted service holds none | Connectors need tokens for Slack, Notion, email | CUT-E27: tokens live in the daemon's secrets store, never the relay |
| Relay carries browser to daemon | Daemon-to-worker links are a new path | CUT-E25 first card (ADR) |

## Epics

| Epic | Outcome |
|---|---|
| E21 Autonomous by default | An agent finishes a day of work with no command or edit approvals; approvals are removed or delegated to a manager agent; the human is asked only for decisions |
| E22 Portfolio at scale | One screen ranks 100 projects by what needs the human, and nothing scrolls past |
| E23 Agents present their work | Any backend can produce and present slides, a wireframe, an animation and a live demo |
| E24 Talk to your agents (March 2027) | A person can answer an agent by voice and take a live call with it |
| E25 Worker machines | A person connects several machines and places each project on one |
| E26 Dev tooling on every worker | A fresh VM becomes a working dev machine (docker, uv, skills, MCP) with one command, checked |
| E27 Connect outside apps | The manager reports to and hears from Slack, Telegram, email, calendar and Notion |
| E28 A front door that sells it | A stranger installs in one line and sees a real, busy dashboard in 30 seconds |

## Order and bets

1. **E21 and E22 first.** Without them the other epics have nothing to show; both build on the manager (CUT-137).
2. **E23 next.** It is the part people will demo. Start with the authoring skill and format spec, because it works across
   backends with no protocol change (ADR-0003, ADR-0036).
3. **E25 and E26 together.** The toolchain epic is the sharpest differentiator (Claude's cloud VMs cannot run a compose
   stack); the workers epic makes it usable. Spike first on what a cloud VM allows.
4. **E27 and E28** in parallel, small and independent. E28 starts now: it is cheap and the gap shows.
5. **E24 is deferred to March 2027.** Text-to-speech narration moves into E23 (CUT-201).

## Risks

- Autonomy without approvals needs a stop that works: spend caps, a kill switch, an undo story (branches, never main).
- Agents presenting HTML is code run next to a signed-in app. ADR-0036's rules (no origin, no network) stay.
- A worker that holds the model key defeats the broker (ADR-0031); keep the lease model on remote workers.
- Cloud VMs differ: nested docker needs a real VM or Sysbox-style isolation, not a locked container.
- Scope: this is a lot. The first slice of each epic is a spike or ADR so the cost is learned early.
