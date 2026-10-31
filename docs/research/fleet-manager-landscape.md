# The multi-agent fleet-manager landscape (research spike, 2026-10-31)

Who else manages many coding-agent sessions across many projects, reachable from a phone or a remote
machine, and how do we compare. Phase 1 (this page) is desk research from vendor pages, docs and
third-party reviews. **Nothing here was run hands-on.** Where sources disagreed, the page says so.
Phase 2 (screenshots, UX and marketing teardown) has a first pass on landing pages (section 8); the rest is planned (section 9). **Section 8 corrects three rows of the tables below, which came from stale desk research.**

## 1. Headline findings

1. **The space has split into three kinds of product, and we sit between them.**
   - Local multiplexers and managers (herdr, Conductor, Claude Squad, Emdash, Paseo): polished, mostly free or open source.
   - Vendor cloud agents (Devin, Cursor cloud agents, Codex, Jules, Copilot Agent HQ, Claude Code cloud sessions, Warp Oz,
     Factory): the agent runs on their servers, usage billing, phone apps.
   - Phone remotes for agents on your own machine (Claude Code Remote Control, Happy, Omnara, Paseo's mobile app).
   - We are a self-hosted daemon with a durable workflow, a journal, a permission inbox and an E2E-encrypted relay.
     Almost nobody combines those.
2. **Anthropic now covers much of our pitch natively for Claude Code.** Remote Control reaches a local session from the
   Claude phone app (QR pairing, server mode with up to 32 concurrent sessions, optional worktree isolation). `claude agents`
   (Agent View, May 2026) groups sessions by Needs input / Working / Ready for review. Claude-only, needs a subscription
   (API keys unsupported), and the transcript is stored on Anthropic's servers while connected.
3. **Paseo is the closest product to us.** Apache-2.0, no account, native iOS and Android plus web and desktop, a daemon you
   run on a laptop, VM or container reached over LAN, Tailscale, SSH or an opt-in E2E relay, 30+ agents, cron `schedule`,
   PR workflow across GitHub, GitLab, Gitea, Forgejo.
4. **herdr is polished and has a one-line pitch.** "Tmux for coding agents": a native desktop app (macOS, Linux, Windows; just
   released), a separate terminal UI, and `herdr-core`, a local server that keeps agents running when the app closes. Per-repo
   spaces, per-task tabs, agent state (working / blocked / done) rolled up to the sidebar, SSH machines in one sidebar, an
   agent-to-agent CLI (`hc run`, `hc send`), mods that the agent itself edits, Apache-2.0. Its docs show no phone app, web UI
   or permission-approval flow. What to learn from it is the clarity and polish, not a feature list.
5. **Free competitors die or stay free.** Vibe Kanban's company shut down on 10 April 2026 (mostly free users); the project
   continues community-maintained. Willingness to pay sits with the cloud agents.

## 2. Product profiles

Backends = how many coding agents the product can drive.

| Product | What it is | Backends | Phone | Remote / multi-machine | Price (as found) |
|---|---|---|---|---|---|
| **herdr** | Open-source native app + `herdr-core` server; spaces per repo, tabs per task | Any terminal agent; status integrations for Claude Code, Codex, Copilot CLI, Cursor Agent, Gemini CLI, OpenCode and more | None in docs | SSH machines in one sidebar | Free |
| **Devin** (Cognition) | Cloud engineer with shell, IDE, browser; from web, Slack, Teams, Linear/Jira, CLI | 1 (its own) | iOS beta waitlist; no Android app found | Cloud VMs | Sources conflict: one page showed Free / $20 / $200 / Teams $80 + $40 per seat; others cite ACUs at about $2.25. Verify on devin.ai |
| **Claude Code Remote Control / Agent View** | Phone, web or desktop window onto local sessions; dashboard of background sessions | 1 (Claude Code) | Yes, Claude app, QR | Local machine, outbound HTTPS only | In Pro, Max, Team, Enterprise |
| **Claude Code cloud sessions** | Tasks in Anthropic's cloud, PR per task | 1 | iOS app | Cloud | Same limits as the CLI |
| **Codex app** | Desktop command center; thread per project, worktree per agent, review queue, scheduled automations | 1 (Codex) | None confirmed | Local; cloud triggers announced | Not captured |
| **Cursor cloud agents** | Isolated VMs with desktop and browser | 1 (Cursor) | iOS (beta per one source); chat and review | Cloud | Sources conflict |
| **GitHub Agent HQ / Mission Control** | One place to assign and track agents from several vendors | Several (Copilot, Claude, Codex, Google, Cognition), preview by plan | GitHub Mobile | Cloud | Copilot tiers |
| **Conductor** | Closed-source Mac app; worktree per workspace, diff review, PR flow. Site now leads with cloud workspaces | 2 (Claude Code, Codex) | Forthcoming | Cloud workspaces on a paid plan | Free local; about $50/month Pro per one listing, unverified |
| **Paseo** | Open-source daemon; desktop, mobile, web, CLI | 6 native, 30+ in a catalog, any ACP agent | Native iOS and Android | LAN, Tailscale, SSH, E2E relay | Free |
| **Happy** | Open-source phone and web client, E2E encrypted; launch with `happy` instead of `claude`. Its site now sells a desktop "harness" | Claude, Codex, Grok, Gemini and more (site), 3 per earlier sources | iOS, Android, web, push | Via `happy-server` | Free |
| **Omnara** (YC S25) | **Stale: now pitched as an open-source alternative to Claude Managed Agents (an API), see section 8.** Earlier: phone and web command center | Claude Code, Codex | Earlier: yes | Earlier: mirrors a terminal | Sources conflict |
| **Claude Squad** | Terminal app on tmux and worktrees; about 8.2k stars; AGPL-3.0 | Several CLIs | None | SSH | Free |
| **Emdash** | Open-source desktop app, worktree per agent | Provider-agnostic | Not found | SSH remotes | Free |
| **Vibe Kanban** | Kanban board for agents; company shut down | About 10 | None | Local only now | Free |
| **Warp Oz / Factory / Jules** | Enterprise cloud orchestration | Mostly their own | Not captured | Cloud (Factory: self-host, air-gap) | Not captured |
| **cuttlefish-crew** | Self-hosted fleet daemon + dashboard | 3 (kopicode, Claude Code, Codex) | PWA via relay or Tailscale https; not run on a real phone, Safari or Web Push | Your machines; hosted relay | Apache-2.0 |

Not covered (nothing usable found): Sculptor beyond a vendor page, Terragon, OpenHands, HumanLayer/CodeLayer.
Paperclip is in `paperclip-comparison.md`.

## 3. User flows worth copying (from docs, not yet watched)

- **Claude Remote Control:** `claude remote-control`, press space for a QR code, scan, session appears in the phone app's Code
  tab. Photos, model and effort changes, and typing on the PC all work at once.
- **herdr:** `hc machine add vps` puts a remote machine's agents in the same sidebar. One space per repo, one tab per task, a
  status dot says which agent needs you.
- **Devin:** tag it in Slack, get a PR back. The work starts where the team already talks.
- **Cursor and GitHub:** start a task from the phone in one line, review a PR there. The phone is for review, not editing.

## 4. How cuttlefish-crew compares

What we have (README, `agent_docs/`): a fleet daemon with several named roles per project; three backends; a Needs-you
permission inbox with a never-allowed list in every mode; a durable satay workflow the daemon resumes after a crash plus an
episodic journal; an encrypted secrets store and optional credential broker; an agent in tmux with a terminal tab; the Chat tab
with Autopilot; device pairing with an E2E relay, Tailscale https and a hosted relay.

| Capability | Us | Closest competitor | Verdict |
|---|---|---|---|
| Many projects in one view | Yes | Paseo, Codex app, Agent View (Claude only) | Equal |
| Number of backends | 3 | Paseo 30+, herdr any terminal agent | Behind |
| Remote permission approval | Yes, strict | Claude app; ours is stricter | Ahead |
| Phone access | PWA | Native apps (Paseo, Happy, Claude, Devin) | Behind |
| Setup friction | `uv sync`, Node, tmux, a CLI on PATH | herdr one-line install; Claude two commands | Behind |
| Crash recovery of a team | Daemon resumes; one-shot CLI does not | herdr survives an app restart, not a reboot | Ahead |
| Audit trail | Redacted episodic journal | Devin and Warp enterprise audit | Equal to ahead for solo use |
| Credential isolation | Broker leases a token | Few do this | Ahead |
| Multi-user and SSO | Single operator, one password | Devin, Cursor, Copilot, Jules, Factory | Behind (accepted gap) |
| Git/PR flow, worktree isolation, scheduled runs | Not checked in this pass | Paseo, Conductor, Codex app | Unknown: read the code first |
| Cloud-hosted agents | No | Devin, Cursor, Jules | Different model |
| First impression, landing page, demo | Pixel-art dashboard, README shots | herdr, Conductor | Behind (phase 2) |

## 5. Market strengths and weaknesses

- Cloud agents: zero setup, parallel by default, team features, work starts from Slack or a ticket. Costs are hard to predict
  (recurring ACU complaint), code runs on their servers, output still needs review, and each is tied to one vendor's agent.
- Local tools: free, private, use the subscription you already pay for. Most have no phone view, no permission handling, no
  crash recovery beyond the terminal's, and no accounts. Sustaining a free product is hard.

## 6. Go-to-market snapshot

- Free open source with a community: herdr, Paseo, Happy, Claude Squad. GitHub, HN, word of mouth, one-sentence hook.
- Freemium with a paid cloud: Conductor (reported), Omnara, Cursor.
- Usage billing: Devin.
- Bundled: Anthropic inside a Claude subscription, GitHub inside Copilot. The hardest to price against.

## 7. Least certain

Devin's pricing and mobile status (one pricing page looked like the Windsurf IDE tiers, which one source says was renamed
Devin Desktop); Codex and Cursor mobile; Conductor and Omnara pricing; whether herdr works from a phone (one write-up says over
SSH, its own docs are silent).

## 8. Phase 2, first pass: landing pages (done 2026-10-31)

Screenshots of 11 public landing pages and ours, desktop (1440 wide) and phone (390 wide), above the fold, are in
`docs/research/assets/landscape/<product>/landing-{desktop,phone}-fold.png`. Full-page captures exist locally but are not
committed (14 MB). The OpenAI Codex page blocked the screenshot (403), so it is not included. Everything below is from the fold screenshots of
herdr, Paseo, Conductor, Happy, Omnara, Devin and ours. **The phone screenshots are captured but their review is on card CUT-228.** No installed app has been run yet.

**Corrections to phase 1 from the live pages** (the desk research was stale on three products):
- **Omnara has pivoted.** Its hero is now "The open-source alternative to Claude Managed Agents: one API to deploy and run
  agents for your users and team", with a CLI/SDK/cURL snippet and 3.3k stars. It is no longer a phone remote for Claude Code.
  Drop it from the closest-competitor set.
- **Conductor now leads with "Run a team of coding agents in the cloud"**, a "Get Conductor" button, a Pricing and an
  Enterprise page, a customer quote, and a multi-person workspace screenshot with a Merge button.
- **Happy is now a desktop "harness"** ("Any Model. Your Subscription. Happy Harness.", Claude, Codex, Grok, Gemini and more),
  free and open source, 24k stars, with downloads for Linux, macOS and Windows.
- **Paseo has 22.6k stars** and its page lists a Linux/macOS download, App Store and Play Store buttons and a CLI, plus "+33 more" agents.

**What their landing pages do that ours does not** (fold only):

| Element | herdr | Paseo | Conductor | Happy | Devin | Ours |
|---|---|---|---|---|---|---|
| Headline says what it is in one breath | "Made for your agents. Remade by them." | "The agentic development environment" | "Run a team of coding agents in the cloud" | "Any Model. Your Subscription." | "Your team's autonomous software engineer" | "Run a crew of coding agents, not a spreadsheet of terminal tabs" (a slogan before a definition) |
| First action | One-line `curl \| sh` install, plus Download | Download for your OS, App Store, Play Store | Get Conductor | Download for your OS | Get started, Book a demo | `git clone` plus Quickstart link |
| Real product screenshot on the fold | Yes, a live app with two agents | Yes, a live workspace | Yes, with a diff and Merge | Yes, with the model menu | Yes, a session with a PR card | A small, low-resolution Fleet screen from an early dashboard with a caption over it |
| Social proof | GitHub link | 22.6k stars on the nav | Customer quote | 24k stars on the nav | Customers page, enterprise banner | None |
| Backends visible | Names in the intro | A row of agent logos and "+33 more" | Named in the copy | Model picker in the shot | n/a | Not on the fold |
| Platforms shown | macOS, Linux, Windows tabs | Linux, Mac, iOS, Android | Download button | Linux, macOS, Windows | Web | None (needs Python, uv, Node) |

**Early read, to be checked against the other screenshots and a hands-on run:**
1. Our fold shows a `git clone`, which says "build it yourself". Every comparable product offers a one-line install or a
   download. A packaged install is the biggest gap.
2. Our hero image shows an empty-looking Fleet screen. It should show the current dashboard with agents mid-task and a Needs-you badge, as
   the competitors show a busy app.
3. We never say how many agents we drive or which ones. Paseo and Happy lead with it.
4. We show no stars, quote or user. Even a plain star count helps.
5. "Not a spreadsheet of terminal tabs" is memorable but the page never says what the product is in plain words until after
   the fold.

## 9. Phase 2, remaining work (planned, not done)

Goal: see their products and pages, then decide what to change in ours. Priorities from the owner: user flows, backend count,
UI/UX, landing page, marketing.

**Targets, in order (landing-page pass done for all but the apps themselves):** herdr (desktop app and site), Paseo, Conductor, Claude Code Remote Control and Agent View, Happy,
Omnara, Devin, Cursor cloud agents, Codex app, GitHub Agent HQ. Add Claude Squad and Emdash if time allows.

**Evidence to collect per target** (screenshots into `docs/research/assets/landscape/<product>/`, one note file each):
1. Landing page above the fold, full page, and mobile width: the headline, the one-line pitch, the demo (video, gif or
   screenshot), the call to action, pricing placement, social proof.
2. First-run: install command, time to first agent, onboarding screens.
3. The main screen with several agents running: layout, how status is shown, what "needs you" looks like.
4. The phone view where one exists: session list, a running session, an approval prompt, notifications.
5. Adding a project and adding a remote machine.
6. Settings that matter: backend choice, permissions.

**Method and limits.** Public pages and docs: Playwright screenshots at desktop and phone widths. Installed apps: install
the free, open-source ones (herdr, Paseo, Claude Squad) in a scratch `HOME` and a scratch directory, never against
`~/.cuttlefish`. Apps behind an account or a paywall (Devin, Cursor, Conductor Pro, Omnara): public pages, docs, demo videos
and store screenshots only, unless the owner agrees to sign up. Run the downloads as untrusted code.

**Output:** a per-product teardown, a side-by-side set of our screens against theirs, and a ranked list of changes to our
dashboard, landing page (`docs-site/`, GitHub Pages), README and demo, each as a candidate board card.

## Sources

[herdr](https://herdr.dev/) · [herdr docs](https://herdr.dev/docs/) · [herdr write-up](https://dev.to/pvgomes/herdr-is-tmux-for-coding-agents-3h2a) ·
[Remote Control docs](https://code.claude.com/docs/en/remote-control) ·
[Agent View](https://rits.shanghai.nyu.edu/ai/anthropic-ships-agent-view-a-multi-session-dashboard-for-claude-code/) ·
[Claude Code on the web](https://claude.com/resources/articles/claude-code-on-the-web) · [Devin pricing](https://devin.ai/pricing) ·
[Devin docs](https://docs.devin.ai/) · [Devin pricing roundup](https://usagebar.com/blog/devin-pricing-and-rate-limits) ·
[Devin Mobile](https://alphasignal.ai/news/cognition-brings-devin-mobile-to-iphone-so-developers-code-on-the-go) ·
[Paseo vs Conductor](https://paseo.sh/docs/alternatives/conductor) · [Conductor](https://www.conductor.build) ·
[Happy](https://github.com/slopus/happy) · [Omnara (YC)](https://ycombinator.com/companies/omnara) ·
[Codex app](https://openai.com/index/introducing-the-codex-app/) ·
[Cursor cloud agents](https://www.morphllm.com/cursor-background-agents) ·
[GitHub Agent HQ](https://github.blog/news-insights/company-news/welcome-home-agents/) ·
[Vibe Kanban shutdown](https://vibekanban.com/blog/shutdown) · [Claude Squad](https://github.com/smtg-ai/claude-squad) ·
[Warp Oz](https://warp.dev/newsroom/2026/2/10/warp-launches-oz-the-orchestration-platform-for-cloud-coding-agents)
