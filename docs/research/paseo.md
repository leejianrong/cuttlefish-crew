# Paseo teardown (CUT-228, 2026-11-12)

Desk research from paseo.sh, its public docs, and the `getpaseo/paseo` repo (README, `CLAUDE.md`, `docs/architecture.md`,
`docs/permissions.md`, `docs/data-model.md`) read through the GitHub API. **Nothing was installed or run.** The desktop app
is a binary download and the mobile apps need a store. Vendor claims are marked as such.

**Correction to the brief: Paseo is Apache-2.0, not AGPL.** The LICENSE file in the repo is Apache-2.0 (with a note that
third-party components keep their own licences); the GitHub API reports `NOASSERTION` only because of that preamble.
The AGPL-3.0 project in this set is Claude Squad (`claude-squad.md`).

## What it is

"The agentic development environment": an open-source desktop, mobile, web and CLI client for coding agents, over a daemon you
run on any machine. It is broader than a fleet manager. It has an editor, terminals, diffs, a built-in browser, pull-request
creation, and voice, in one window with split panes. 23.8k stars, v0.15.4 released 2026-11-10, TypeScript, one maintainer
named in the licence (Mohamed Boudra) and a sponsor strip.

## Features

- **Providers.** Custom adapters for Claude Code (via its SDK), Codex (app-server), OpenCode, Pi, OMP, Antigravity, Muse Code,
  Copilot (ACP); any ACP agent besides. The site says "+33 more". Paseo "doesn't extract tokens or call inference APIs
  directly" (FAQ).
- **Workspaces and worktrees.** A workspace is where a task runs; a worktree-backed one gets its own directory and branch under
  `$PASEO_HOME/worktrees/`. Modes: branch off, check out a branch, check out a PR (`--mode checkout-pr`). Lifecycle: create,
  run setup, launch agents, review the diff, merge or archive; teardown runs when the last workspace on a worktree is archived.
  `paseo.json` holds `worktree.setup`, `worktree.teardown`, named scripts and long-running services; each service gets a port
  and a hostname through the daemon's reverse proxy, so every worktree has its own dev-server URL.
- **Review and PRs.** Diff panel ("Changes" and "Files"), a View PR button, PR status per workspace in the sidebar, forge
  support for GitHub, GitLab, Gitea and Forgejo (`docs/forge-providers.md`).
- **Orchestration.** Agents can create worktrees, launch other agents and message them, across providers, through a tool
  catalog exposed as MCP, the CLI (`paseo run/ls/logs/wait`) and a TypeScript SDK. Schedules (cron) run agents, and archive
  on finish by default.
- **Plugins.** The daemon hosts them and every client shows them, mobile included (`paseo plugin add owner/slug`). The site
  says 400+ community plugins (vendor count). The headline demo is an agent writing its own sidebar panel.
- **Voice.** Local speech-to-text; optional OpenAI providers.
- **Paseo Hub** (optional): GitHub, Slack and Discord triggers that start an agent on your daemon. Self-host free with
  `npx @getpaseo/hub`, or hosted: Free $0 (50 agent runs, one seat), Pro $15 per seat per month, unlimited (hub page, 2026-11-12).
  Team daemon provisioning is "planned, not built".

## Architecture

From `docs/architecture.md`:

```
 mobile (Expo) / CLI / desktop (Electron) / web
        |  WebSocket (direct or via relay)
     Daemon (Node.js)  ->  provider adapters (Claude SDK, Codex server, ACP, OpenCode, Pi ...)
```

- **Daemon** (`packages/server`): HTTP plus WebSocket server; agent manager with a lifecycle state machine and a timeline model;
  file-backed JSON persistence in `$PASEO_HOME/agents/` (zod schemas, atomic writes, "no migrations", `data-model.md`);
  schedule module; MCP adapter; outbound relay transport.
- **Terminals live outside the daemon.** PTYs run in a detached terminal worker that survives daemon restarts, crashes and upgrades.
- **Packages:** `protocol` (wire schemas, the source of truth), `client` (SDK), `app` (one Expo codebase for iOS, Android and
  web; desktop is Electron around it), `cli`, `relay` (Elixir in the security docs), `desktop`, `website`.
- **Default port** `127.0.0.1:6767`; config in `~/.paseo/config.json`; log in `daemon.log`.
- **Protocol compatibility** is handled as a first-class problem: app and daemon versions drift, so features are gated on capabilities
  (`protocol-compatibility.md`).
- **No durable workflow engine.** An agent run is a provider session the daemon supervises; the repo docs describe resume and
  archive of agents, not a replayable journal. I did not look for one beyond the docs list.

## Remote access and security (paseo.sh/docs/security)

- Three modes: **relay** (outbound only; open-source relay, off on a fresh install, opt in at pairing or with `--relay`),
  **direct** (loopback by default; bind a VPN address with a password), and a **Unix socket** for the CLI only.
- Relay handshake: persistent ECDH keypair in `daemon-keypair.json`; the QR or pairing link carries the daemon's public key;
  client sends its own key; Curve25519 shared secret and a NaCl `box` channel; fresh keys per session. The relay sees IPs,
  timing, sizes and session IDs. The pairing link is the trust anchor ("treat it like a password"). A separate doc
  (`relay-endpoint-security.md`) mentions a Noise NK mapping and a `requireProtectedSecurity` flag.
- Direct access: bcrypt-hashed password, `Authorization: Bearer`, a `Host`-header allowlist against DNS rebinding.
- **Permissions** (`docs/permissions.md`): principals with additive grants (`workspace.read`, `.write`, `.manage`,
  `daemon.manage`, `access.manage`, `automation.manage`...), credentials per device, single-use pairing invitations,
  Owner/Operator/Viewer as UI presets. This is device and user authority, not per-tool-call approval policy; an agent's own
  permission prompts are shown in the client (the hero shot has a shield icon in the composer), but I found no never-allowed list
  or approver tier in the docs.
- Agent credentials stay with each provider; Paseo stores no provider keys.

Compare ours: ADR-0037/0039 (E2E relay, pairing, tiers) cover similar ground; ours also adds a manager-agent approval tier and
a hard never-allowed list.

## User workflows

1. Install the desktop app (its own daemon starts); add a project; "New workspace" off `origin/main`; launch an agent.
2. Fan out labelled GitHub issues to one worktree agent each; review each diff; open PRs; archive.
3. Pair the phone with a QR code, work in the same sessions on the native app (the site claims "full feature parity").
4. Run the daemon headless on a VM or Docker (`PASEO_PASSWORD`), connect from anywhere.
5. Ask an agent for a plugin; it scaffolds and installs it, and the new panel shows on the phone too.

## Screenshots (internal comparison only)

Kept for internal comparison, not for publishing. Sources:

| File | Source URL |
|---|---|
| `assets/teardown/paseo-hero-mockup.png` | https://paseo.sh/hero-mockup.png (also embedded in the repo README) |
| `assets/teardown/paseo-mobile-mockup.png` | https://paseo.sh/mobile-mockup.png |
| `assets/landscape/paseo/landing-*-fold.png` | our own Playwright capture of https://paseo.sh (2026-10-31) |

What the hero shows: a left sidebar of projects with worktree rows, each with `+lines -lines` and a PR number and state;
a chat timeline with shell tool calls; a composer with provider, effort and a red shield; a right-hand diff panel; and the same
session on a phone in a compact layout. No Jian screenshots exist. The site embeds no video.

## What I could not verify

Everything that needs a running app: how a permission request looks and where it is announced, whether the "full parity" phone
claim holds, push notifications, relay latency, what "stuck" or "needs input" looks like across many sessions, whether it copes
with 100 projects. Which of the 400 plugins matter. The 23.8k stars and the vendor's testimonials are unaudited.

## Sources

[paseo.sh](https://paseo.sh) · [security](https://paseo.sh/docs/security) · [configuration](https://paseo.sh/docs/configuration) ·
[worktrees](https://paseo.sh/docs/worktrees) · [hub](https://paseo.sh/hub) ·
[Paseo vs Conductor](https://paseo.sh/docs/alternatives/conductor) (Paseo's own comparison, marketing) ·
[repo](https://github.com/getpaseo/paseo): README, LICENSE, `CLAUDE.md`, `docs/architecture.md`, `docs/permissions.md`, `docs/data-model.md`.
