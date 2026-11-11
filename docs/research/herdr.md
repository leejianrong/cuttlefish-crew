# herdr teardown (CUT-228, 2026-11-12)

Desk research from the product site, its docs and READMEs, and the public GitHub API. **Nothing was installed or run.**
The desktop app is a download that needs a desktop session (Vulkan on Linux), and the installers are `curl | sh`, which this
spike does not run. Where a claim comes only from the vendor's own page, it says so. Background and the earlier landing-page
pass are in `fleet-manager-landscape.md`; this page adds what that one lacked.

## What it is

A native desktop app for terminals and coding agents, plus the server that actually owns the terminals. One sentence from the
site: "Made for your agents. Remade by them." The hook is that you ask your agent to change the app, and it edits plain files.

Three things ship under one name, and the site is careful to keep them apart:

| Piece | What it is | Repo | Licence |
|---|---|---|---|
| Herdr app | Native desktop UI (Rust). Spaces, tabs, panes, agents sidebar | `herdrdev/herdr-app` | Apache-2.0 |
| herdr-core (`hc`) | Local server that runs the terminals and agents. Draws nothing. CLI plus an API | `herdrdev/herdr-core` | Apache-2.0 |
| Herdr TUI | The original terminal version with its own server. Its agents do not appear in the app | `herdrdev/herdr` | Apache-2.0 |

Free, no paid tier on any page. The GitHub API listed about 2.7k stars on the app, 620 on core, and 50k on the TUI repo (the
TUI is the older, better-known project; I did not check how the number was reached). Latest TUI release v0.9.5 (2026-10-14);
the app and core have no GitHub releases, they ship from `releases.herdr.dev`.

## Features

- **Spaces, tabs, panes.** A space holds work on one machine, usually one repo. Tabs per task, panes inside a tab for terminals
  or "mod panels". An unnamed space follows its first terminal's folder.
- **Agent status, rolled up.** Working, Blocked (waiting for you), Done (finished while you were elsewhere), Idle. An agents
  sidebar lists every agent from every space, including helpers another agent started. Click one to "peek" over the current tab.
- **Status is a protocol, not scraping.** Programs print an OSC 7501 escape sequence (`printf '\e]7501;state=blocked:app=my-agent\e\\'`).
  The core "doesn't scrape screens or keep a list of known agents" (core page). Claude Code, Codex and others have integrations that emit it.
- **Agents start agents.** `hc run --bg --name review codex "..."`, then `hc wait`, `hc read`, `hc send`. Helpers live under the
  agent that started them and stop with it. `hc --skill` prints the manual for the agent to read.
- **Survives the window.** Closing the app leaves terminals running; closing a pane or tab ends them. After a reboot or core
  crash, kept panes get fresh shells in their last folders, not their old programs (app README). So this is persistence, not recovery.
- **More than one machine.** `hc machine add box@my-server`; SSH hosts come from your SSH config. A remote space runs its
  terminals there, keeps running when your laptop sleeps, and shows in the same sidebar. Terminals never touch herdr's servers.
- **Mobile apps (iPhone, iPad, Android).** The earlier landscape note said herdr had no phone app; that is out of date. See below.
- **Customisation.** Layouts, sidebar, tabs, keys and themes are plain files in `~/.config/herdr-app` with an `AGENTS.md` that
  explains them to the agent. The site gallery shows 30 community layouts and cites 576 mods (a vendor count).

## The phone app

From the mobile page (vendor claims, not tested):

- It connects to each machine's herdr-core **over SSH**. No account, no herdr server, no relay. Away from home you use
  Tailscale or another VPN; Tailscale SSH works without a key. After the first machine it suggests others from your SSH config
  and Tailscale devices.
- **Inbox**: all agents on all machines, grouped Needs you / Done / Working / Idle, a blue dot for unread, swipe right to pin or
  mark read, swipe left to send Esc, an in-app banner when an agent needs you or finishes (only while the app is open; the page
  mentions no push notifications).
- **Terminal**: the agent's real terminal, with an Esc/Ctrl/Tab/arrow key bar. A compose mode takes long text, photos or
  screenshots and dictation, and sends it as one paste plus Enter.
- On a foldable or iPad the sidebar and terminal sit side by side.
- No sign-in, ads or analytics.

What this means: the phone is a **terminal client with a status inbox**. An answer to a permission prompt is you typing into
the agent's own terminal. There is no structured approve/deny and no policy, which is consistent with the core's security
page: core "is not a sandbox or a permission broker for agents", and a status of blocked "is not an access boundary".

## Architecture and security (from `herdr-core/docs/security.md` and README)

- Local daemon (`hc`), started on demand by the CLI. "Holder processes" own the terminals so a core restart or `hc update`
  leaves programs running.
- Transport: a **Unix socket** on Linux and macOS (state dir mode 0700), named pipes on Windows. No TCP port, "no separate API
  password or per-client permission check". Anyone who can reach the socket can run commands as you.
- Remote: the same protocol carried over an SSH connection. SSH does login, host keys and encryption.
- API: `hc schema` prints it as OpenRPC with streams and events. The page does not say what wire framing it uses.
- State: per-session directories (`HC_SESSION` selects another core). Sessions are "not security boundaries".
- Install: `curl -fsSL https://releases.herdr.dev/app/install.sh | sh`, a `.dmg`, `brew install --cask herdr-app`, or a
  PowerShell script. The README says to read the installer first. It installs core too.

## User workflows

1. Install with one line, open the app, open a folder as a space, start `claude` in a tab. A dot shows its state.
2. One space per repo, a tab per task; glance at the sidebar to see who is Blocked.
3. Ask an agent to start a reviewer (`hc run --bg`), keep working, read its report when it lands in your agent's input.
4. `hc machine add`, then start agents on the VPS; close the laptop; answer from the phone's Inbox by typing into the terminal.
5. Tell the agent "put a timer on each tab" and watch it edit the config files.

## Pricing and licence

Free; Apache-2.0 for all three repos. Nothing on the site names a company or a paid plan, so how it is funded is unknown.

## Screenshots (internal comparison only)

Source: the vendor's own pages. Kept only for comparison; do not publish them. Details are in `ux-teardown-comparison.md`.

| File | Source URL |
|---|---|
| `assets/teardown/herdr-mobile-inbox.webp` | https://herdr.dev/mobile/inbox.webp |
| `assets/teardown/herdr-mobile-terminal.webp` | https://herdr.dev/mobile/terminal.webp |
| `assets/teardown/herdr-mobile-foldable.webp` | https://herdr.dev/mobile/foldable.webp |
| `assets/landscape/herdr/landing-*-fold.png` | our own Playwright capture of https://herdr.dev (2026-10-31) |

The hero's app screenshot is built in the page rather than a hosted image, so there is no separate file; the landing fold
capture shows it. No Jian screenshots exist (`~/teardown-shots/` was absent). No demo video was found on the site.

## What I could not verify

Everything about the running app: how fast status changes, what a Blocked agent looks like, the peek view, whether the app
needs an account for anything. Whether the phone app gets push notifications. The 50k-star figure's meaning. How the OSC 7501
integrations are installed for Claude Code and Codex (the page says integrations exist, not how).

## Sources

[herdr.dev](https://herdr.dev) · [core page](https://herdr.dev/core/) · [mobile page](https://herdr.dev/mobile/) ·
[herdr-app README](https://github.com/herdrdev/herdr-app) · [herdr-core README and security.md](https://github.com/herdrdev/herdr-core) ·
GitHub API repo metadata, fetched 2026-11-12.
