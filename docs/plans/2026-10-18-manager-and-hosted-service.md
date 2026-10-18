# Plan: one manager, agent-written handoffs, and a hosted multi-user service

Recorded 2026-10-18 from a planning discussion with Jian. The same content, drawn as diagrams, is in a private
artifact; this file is the version that lives with the code. Board: epics CUT-E17, CUT-E18 and CUT-E19 on board 23.

## What Jian wants

1. **Efficacy.** Even the local cuttlefish-crew driving kopicode must keep agents working "comfortably for hours or
   days", so that a person checks in about once a day or less and the agents code in the background.
2. **Handoffs are the agent's job.** cuttlefish should tell the agent to write a handoff and take it back. It must not
   write handoffs itself.
3. **cuttlefish's own model is a high-level manager and summariser**, one across all projects: it consolidates progress
   from about 20 projects and reports to the human to reduce cognitive load. It is also the interface to the human,
   reachable from a phone through the dashboard, remotely.
4. **Later:** voice or video with the manager, and demos of apps, or at least pictures and videos.
5. **A hosted service on DigitalOcean**, deployed only through CI/CD, built for many users from the start. The same
   service later opens to the public: sign up, GitHub or Google sign-in, onboarding, then 20 to 100 agents on the user's
   own PC monitored from a phone.

## What already existed

- kopicode's `session.handoff` (kopicode ADR-0026): one model call with no tools, from the session's own history, plus a
  facts block from kopicode's journal. kopicode leaves "when to hand off" to the caller. cuttlefish never called it; it
  wrote its own summary from a journal that lists tool calls but not their output (ADR-0004, ADR-0030).
- ADR-0032 (decided, V6): one long-lived agent session per project, with the satay-workflow question left open.
- The relay, pairing, devices, tiers and revocation (ADR-0037, ADR-0039), Tailscale https (ADR-0041), and the claim in
  ADR-0037 that accounts and hosting do not ship before an outside security review.

## Decisions (Jian, 2026-10-18)

| | Decision | Where it landed |
|---|---|---|
| Handoff | The agent always writes it. No real-task comparison first. | ADR-0042, CUT-134 (kopicode, done), CUT-140 (Claude Code, Codex, then delete the summariser) |
| Manager | **One** manager across **all** projects, on a cheap OpenRouter model (deepseek flash class), **$1 a day in total**. | CUT-137, CUT-151 |
| Credentials | Coding agents run on subscriptions on the user's PC. The manager never needs them; the hosted service never sees them. | CUT-104 (policy memo) |
| Deploys | Only through CI/CD. The first Droplet setup may be by hand. | CUT-141, CUT-143 |
| Tenancy | Multi-user from the start; public later, behind the outside review. | CUT-142 (ADR), CUT-145 to CUT-149 |
| Voice and video | **Deferred.** The aim is not to rebuild Zoom, Meet or Discord. Pictures and clips in updates stay. | CUT-139 (deferred), CUT-138 |
| Droplet | Jian creates it later. A plain Droplet with Caddy, not App Platform (websocket support unconfirmed). | CUT-143 |

## Shape

**Handoff.** At a checkpoint (turn cap, token budget, a full context) cuttlefish asks the agent for its handoff before
the session closes, journals the document unchanged as the handover, and starts the next session from it. If the draft
cannot be had, the round falls back to a digest (today: the old summary). See ADR-0042.

**Manager.** One event-driven supervisor, a satay workflow, so a crash resumes supervision. It wakes on events and
timers (a round ends, an agent stalls, a request is raised, the human writes, a daily timer), reads a compact digest per
project and takes one of a few actions: tell an agent, raise a Needs you card, post an update with an attachment, wait.
It never reads transcripts and cannot lift the guard rails (never-allowed commands, permission modes, redaction,
budgets). Every message to or from the human is a journal event, so there is no second transcript (ADR-0004). Spend is
capped at $1 a day across all projects. What a digest may contain (no code, no secrets) is its own card (CUT-150).

**Hosted service.** The relay serves the dashboard page same-origin on a Droplet behind Caddy. It holds accounts, which
rooms belong to whom, online state and byte counts, and nothing else: no project data, model keys or agent credentials.
A room is claimed with a token bound to an account. Limits per account and per address, and a way to suspend an account.
Deploys: a PR runs the existing checks; a merge builds the image and records its digest; a deploy job in a protected
production environment (approval at first) reaches the Droplet over a key restricted to one deploy script; a health check
runs, and a failure restores the previous digest. A smoke test (page loads, a daemon registers a room, a browser pairs)
runs against the image before it takes traffic.

## Order

0. Small fixes (done: CUT-133).
1. Handoff by the agent: kopicode (done, CUT-134), then Claude Code and Codex (CUT-140).
2. Relay hosting and CI/CD (CUT-135, CUT-141, CUT-143, CUT-144), after the tenancy ADR (CUT-142) so the relay is written
   against the real model. All testable without the Droplet.
3. Soak harness (CUT-136), so there is evidence before the manager is trusted.
4. The manager (CUT-137), with its digest rules and spend cap (CUT-150, CUT-151), then media in updates (CUT-138).
5. Accounts, onboarding, limits, terms and the outside review (CUT-145 to CUT-149) before any public sign-up.

## Open

- Whether the single-user phase uses a simple sign-in before GitHub and Google are added. Leaning yes, with the real
  account table and room ownership from the start.
- Whether deploys need approval each time or only for the first few.
- A real model's handoff quality on real tasks is unmeasured; the soak harness is where it will show.
- A manager over many projects sends a digest of each project's progress to a third-party model. The digest rules
  (CUT-150) decide what that exposes.
