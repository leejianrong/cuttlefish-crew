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
  *(Update, KAN-1809: this gap is closed. `Project.backend` and a per-role
  backend now exist, so the paragraph below describes the state before
  that card. Mixed-backend crews are real.)*
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

## P2 — Non-technical solo founder shipping with coding agents (KAN-1698)

**Story**: As a non-technical solo founder using Claude Code/Codex to build
my product, I want a fleet manager that turns "build me X" into a tracked,
reviewable unit of work I can approve without CLI fluency, so I can direct
development safely without reading code.

**Verified against the current codebase (2026-09-28)**:

- **A reviewable, approvable unit of work with no CLI needed — real.**
  `--require-approval`/`ApprovalDecision` (KAN-1711, ADR-0016) blocks a
  round from finalizing until a human decides; the dashboard's
  `RoleSteerCard.svelte` renders a point-and-click Approve/Reject panel
  whenever a role's status is `"blocked"` (verified directly:
  `RoleSteerCard.svelte:108` gates the panel on `status === "blocked"`) —
  Approve needs no comment, Reject requires one (`RoleSteerCard.svelte:39`'s
  own comment explains why: a comment-less reject would just round-trip a
  400). No terminal required for the day-to-day approve/reject loop.
- **Registering and starting a project with no CLI needed — also real.**
  `RegisterProjectForm.svelte` posts directly to `POST /api/projects` with
  name/root/roles/allow/budget fields — an operator never has to type a
  `cuttlefish` command to stand up a new project's crew once a daemon is
  already running.
- **The honest remaining gap: getting the daemon itself running still is a
  CLI step.** `cuttlefish serve` (or the systemd unit that supervises it,
  ADR-0014) is how a fleet daemon starts existing today — there's no
  installer/onboarding flow yet that a non-technical founder could complete
  without ever opening a terminal once. That's exactly the gap KAN-1719
  (hosted MVP) is scoped to close ("sign up and get a running fleet daemon"
  rather than self-host); until it ships, this persona's "without CLI
  fluency" claim is true for *operating* a project day to day, not yet for
  *getting started* from zero.

## P3 — Solo digital designer / video editor (KAN-1699, plausible expansion)

**Story**: As a solo designer/editor, I want a crew of agents that can
execute production tasks (batch-export assets, apply a style guide, render
social crops) across several client folders, with the same dashboard/
approval model as the coding case.

**Key finding this card asked to validate**: does this need a new
`AgentBackend`, or does the existing allowlist mechanism already reach
non-coding shell tools (ffmpeg, ImageMagick) with no special-casing?

**Verified directly against the source (2026-09-28), not assumed**:

- `cuttlefish.delegate.policy.write_policy_file` — the function that turns
  an operator's declared `--allow`/`Project.allow` into kopicode's own
  permission-gate file — takes `allow: list[list[str]] | None` and
  literally `json.dumps`s it into kopicode's grammar with **zero
  command-name filtering of any kind** (`policy.py:35-48`). There's no
  built-in notion of "this is a coding tool" versus "this isn't" anywhere
  in that path — an allow entry of `["convert", "*"]` or `["ffmpeg", "-i",
  "*"]` is written and honored exactly the same way `["git", "commit"]` is.
- Every backend's own `delegate()` threads the identical `allow:
  list[list[str]] | None` shape straight through
  (`cuttlefish.agents.kopicode`, `cuttlefish.agents.claude_code`,
  `cuttlefish.agents.codex` all take the same parameter, unchanged) — none
  of the three backends adds its own coding-specific restriction on top.
- `ffmpeg`/ImageMagick's `convert` are both real, ordinary binaries on this
  machine (`/usr/bin/ffmpeg`, `/usr/bin/convert`) — nothing about running
  them through this mechanism is hypothetical at the plumbing level.
- **What this does *not* verify**: an actual end-to-end run where a
  backend's own LLM decides, unprompted, to invoke `convert`/`ffmpeg` for a
  "batch-export/apply a style guide/render a social crop" task. That needs
  a real paid LLM call through kopicode/Claude Code/Codex, which needs the
  same credentials `.env`/`CUTTLEFISH_SECRETS_KEY` already gate — deliberately
  not exercised in this pass (CLAUDE.md's own secrets boundary), so treat
  "no new AgentBackend needed" as **verified at the policy/plumbing layer,
  not yet at the full agentic-task layer**. The card's own ask ("validate
  with one real non-code workflow") is only half done — a real run,
  ideally by Jian directly against one of his own client folders with his
  own already-authenticated `kopicode`/`claude`/`codex` login, is the
  remaining step before positioning copy calls this persona "validated."
- Packaging/marketing implication holds regardless: this persona's gap is
  UX and copy (a non-coder shouldn't have to read this project's own
  developer-facing docs to know it works for JPEGs, not just source files),
  not new infrastructure.

## P4 — Small agency/team bridging solo to multi-operator (KAN-1700)

**Story**: As a small agency owner with a few contractors each juggling
multiple client projects, I want one dashboard showing every project's
crew status company-wide even though different humans nominally own
different projects.

**Verified against the current codebase (2026-09-28)**:

- The portfolio dashboard itself is real and already company-wide in
  shape — one `cuttlefish serve` process's `/api/projects` lists every
  registered project regardless of who registered it, no per-project owner
  field or filter exists to *not* show one to another operator.
- That's exactly the gap, not a feature, for this persona: `cuttlefish
  serve`'s non-loopback auth (`SessionAuth`, ADR-0011) is honestly
  documented as "single-operator auth, not multi-tenancy — one shared
  password, one class of session token, no per-user accounts or roles."
  Every contractor who can log in sees and can steer/approve/stop *every*
  project, not just their own client's. There is no isolation between
  operators today at all.
- This confirms the card's own framing precisely: it's a bridge persona
  that surfaces the multi-tenancy/isolation question (ADR-0002's addendum,
  trigger 2) *before* the full enterprise story (P5 below) forces it — it
  doesn't answer that question. Positioning copy for this persona should
  say "one dashboard for your whole team to see" (true) and stop short of
  "each contractor only sees their own clients" (not built) or "safe for
  agencies with contractors you don't fully trust with each other's client
  data" (actively false today).

## P5 — North star, phase-2: every employee manages a crew (KAN-1701)

**Story**: As an employee at a company, I want my own dashboard managing my
personal crew of agents against my assigned work, with company-wide
visibility, budgets, and governance for my manager.

**Deliberately not validated against the codebase — this is stated
ambition, not a near-term build target.** It requires exactly what P4 named
as missing (multi-tenancy/isolation between operators) plus SSO/RBAC and
org-level budget rollups on top — the identical org-chart/governance
surface the positioning decision ([[project_paperclip_positioning]], "crew
not company") explicitly declined to chase as a near-term differentiator
against Paperclip. Worth stating in GTM materials as "where this could go,"
never as "what cuttlefish-crew does today" — revisit only once the solo/
solopreneur wedge (P1-P3) is proven, per the card's own text, not before.
