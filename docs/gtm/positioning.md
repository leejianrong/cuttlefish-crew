# Positioning: the wedge vs. Paperclip (KAN-1716)

A citable messaging artifact — turns the 2026-09-25 competitive analysis
([[project_paperclip_positioning]] in this project's own agent memory) plus
the five settled persona cards (`docs/gtm/personas.md`) into copy other
CUT-E11 cards (KAN-1717 landing page, KAN-1720 launch plan) can lift from
directly, re-verified against live sources rather than restated from memory
(memory is 3+ days old on a project that moves daily).

## The competitor, re-verified 2026-09-28

`paperclipai/paperclip` (`gh api repos/paperclipai/paperclip`): MIT-licensed,
created 2026-03, **89,399 stars, 15,616 forks, 5,829 open issues, ~202
contributors** (up from ~82k stars / ~219 contributors on 2026-09-25 — still
gaining roughly 2,500 stars a day). Models agents as employees in an org
chart — hiring approval, budgets, performance reviews, SSO/GRC/RBAC.
Monetizes via an emerging first-party hosted "Cloud" tier plus an
unaffiliated third-party managed-hosting reseller. VC-velocity output, not a
hobby project, and still accelerating.

**Do not try to win this on raw feature parity or release velocity.** A
~200-contributor, weekly-release project outbuilds a much smaller effort on
sheer surface area by construction. The wedge has to be a different shape
of value, not a longer feature list.

## The wedge: "crew, not company"

Paperclip's own framing: *"if OpenClaw is an employee, Paperclip is the
company."* cuttlefish-crew's answer is not a smaller company — it's a
different unit entirely: **a lighter-weight crew of 2-3 named coding agents
per project**, not a simulated org. Concretely, three things a solo operator
gets here that Paperclip's own shape doesn't optimize for:

1. **A lighter mental model.** No org chart, no hiring workflow, no
   performance reviews. Register a project, name a few roles (a builder, a
   reviewer, an ops-checker), and direct them — the whole surface a solo
   developer needs to learn is "projects and roles," not "departments and
   headcount." Verified real in `docs/gtm/personas.md` P1: N named roles run
   concurrently per project (ADR-0007), portfolio-wide from one dashboard
   (ADR-0009).
2. **Deterministic satay-replay durability.** Every workflow is a durable
   satay execution from its first commit (ADR-0001) — a crashed CLI or a
   restarted daemon resumes a non-terminal run from its own `run_id`, not
   from scratch (ADR-0010). This is a real architectural difference, not
   just a claim: Paperclip's own continuity model is DB-backed "heartbeats"
   across a persistent service, a different mechanism aimed at a different
   deployment shape (an always-on multi-tenant service) than cuttlefish's
   (an operator's own long-running process). **Caveat, don't oversell**:
   "Paperclip-par or better" was Jian's own explicit bar (`[[project_paperclip_positioning]]`)
   for the continuity epic (CUT-E8, now shipped), but neither side's
   continuity has been stress-tested under one shared, comparable real-world
   load test — this is an architectural claim, not a benchmarked one.
3. **A personality-per-role tone, not a board-of-directors framing.** Each
   role renders as a small animated pixel-art sprite with its own status/
   pose (Slice D2) — a deliberately different emotional register than
   Paperclip's governance/hiring-approval aesthetic. This is a taste bet,
   not a technical differentiator; name it as such in any copy, don't dress
   it up as a capability.

## Where the wedge is strongest, where it isn't (per persona)

- **P1 (solo developer, 10-100 projects) is the strongest and only fully
  validated claim** — Jian himself, not hypothetical. Lead every piece of
  GTM copy with this persona.
- **P2 (non-technical founder)** needs the approval-gate workflow more than
  anything else, and it's real (KAN-1711) — but "no CLI needed at all" is
  only true once a daemon is already running; getting one running from zero
  still needs a terminal until KAN-1719 ships. Don't claim zero-terminal
  onboarding yet.
- **P3 (designer/editor)** is a plausible expansion with the mechanism
  verified at the source (the allowlist has no coding-specific filtering)
  but *not yet* verified end-to-end with a real agentic run. Don't lead
  positioning with this persona until that validation actually happens —
  it's explicitly flagged half-done in `personas.md`.
- **P4 (small agency)** surfaces a real, currently-true limitation: zero
  operator isolation. Any copy aimed at agencies must not imply
  per-contractor data scoping — it doesn't exist.
- **P5 (every-employee-manages-a-crew)** is explicitly out of near-term
  scope (KAN-1715's own non-goal). State it only as ambition, never as
  present capability.

## What this project explicitly does not chase (KAN-1715, logged non-goal)

Org charts, hiring-approval workflows, performance reviews, SSO, GRC, RBAC,
or multi-tenant operator isolation as a near-term goal. Not because these
are bad ideas — because racing an ~200-contributor, weekly-release project
on its own chosen surface, as a much smaller effort, is not a winnable
fight, and Paperclip's own "issue-boundary review gate" governance shape is
already matched at the equivalent scope by cuttlefish's own round-boundary
approval gate (ADR-0016) without needing the org-chart wrapper around it.
Don't re-propose chasing this surface without re-litigating "crew, not
company" with Jian directly first.

## One-line positioning statement (for landing-page/launch-plan copy)

> cuttlefish-crew is a fleet manager for a *crew*, not a *company*: register
> your software projects, name a small team of coding agents per project
> (Claude Code, Codex, or kopicode), and direct all of it — durably,
> resumably, remotely — from one dashboard. No org chart required.
