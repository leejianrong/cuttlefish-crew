# GTM personas (CUT-E11)

A living reference for cuttlefish-crew's target personas, one entry per
Pandan card (KAN-1697 through KAN-1701). Each entry states the user story
as positioning/landing-page copy would want to read, then what's actually
verified against the current codebase — the same "name the gap, don't
paper over it" discipline the engineering ADRs already use (see e.g.
ADR-0017, ADR-0018), applied to marketing claims instead of code. Feeds
KAN-1716 (positioning doc), KAN-1717 (landing page), and KAN-1718 (docs
site) directly — grounding, not a replacement for those.

## P1 — Solo developer running 10–100 coding-agent sessions (KAN-1697)

**Primary, validated persona: Jian himself, not hypothetical.** Every other
epic in this project is being built to satisfy this story first.

**Story**: As a solo developer, I want to register 10-100 of my software
projects and run a small crew of coding agents (Claude Code, Codex,
kopicode) per project, so I can direct work across my whole portfolio from
one dashboard instead of babysitting N terminal tabs, with context/
lifecycle managed for me and remote access from anywhere.

**Verified against the current codebase (2026-09-28)**:

- **Registering 10-100 projects — real, no artificial ceiling.**
  `cuttlefish.projects.ProjectStore` (`~/.cuttlefish/projects.db`) is a
  plain SQLite table keyed by project id; `FleetDaemon` launches every
  registered project's team concurrently as its own `asyncio` task in one
  process (ADR-0009), no per-project subprocess and no hard-coded cap.
  Actual behavior at 100 concurrently-running teams has never been load-
  tested, though — real, but scale is asserted from architecture, not
  measured.
- **One dashboard, whole portfolio — real.** `cuttlefish serve`'s HTTP
  surface (`cuttlefish.fleet.server`) exposes list/get/events/start/stop/
  steer/approve/roles/allow/budget per project; the Svelte dashboard
  renders a portfolio grid plus a per-project detail view (Slice D1/D2).
- **Context/lifecycle "managed for me" — real, with a named shape.**
  Handover auto-summarizes on a token-budget threshold
  (`cuttlefish.handover`, ADR-0004/0010); a crashed CLI or a restarted
  daemon resumes a non-terminal run via `satay.start` with its original
  `run_id` (`resume_pending`, ADR-0010) instead of losing it. One caveat: a
  `--user` systemd unit needs `loginctl enable-linger` to survive the
  operator logging out, and `scripts/install-systemd-service.sh` only
  warns about this, it doesn't enable it (ADR-0014).
- **Remote access "from anywhere" — partially real; be precise in copy.**
  Verified real: non-loopback bind with password/session auth (ADR-0011),
  same-origin dashboard serving (ADR-0012), and a direct tailnet bind
  (`--tailscale`, ADR-0013). Not yet real: an actual live public
  deployment — `Dockerfile`/`fly.toml.example` (ADR-0015) have only run
  against a local Docker daemon, never Fly's real service (KAN-1719 is
  still in `todo`). So today "from anywhere" means "anywhere your
  Tailscale/VPN reaches," not "any browser, no client install" — accurate
  copy should say the former until a hosted MVP actually ships.
- **"A small crew of coding agents (Claude Code, Codex, kopicode) per
  project" — the *team* half is real, the *mixed-backend* half is not.**
  N named roles do run concurrently via `satay.gather` (ADR-0007). But
  `CUTTLEFISH_AGENT_BACKEND` is resolved once per process
  (`cuttlefish.config.resolve_agent_backend`), and neither `Project` nor
  `RoleInput` has a per-project or per-role backend field (checked
  directly in `cuttlefish.projects.store`/`cuttlefish.team`) — every
  role, in every project, running under one `cuttlefish serve` process,
  shares whichever single backend that process was started with. A crew
  of several roles all on the *same* backend, per project, is real; mixing
  Claude Code + Codex + kopicode within one team, or running two projects
  on different backends under one daemon, is not yet possible. Positioning
  copy should say "your choice of Claude Code, Codex, or kopicode" (true),
  not "mix agents within one crew" (not yet true).

**Implication for other CUT-E11 cards**: the "crew" pitch is strongest
exactly where it's true — one dashboard, many projects, each running its
own named, resumable, remotely-steerable team. Any copy implying per-role
backend mixing needs a caveat, or a real follow-up engineering card (not
proposed here — out of scope for a persona write-up, just named so
KAN-1716 doesn't inherit the overstatement).
