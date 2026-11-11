# Orca teardown (CUT-228, 2026-11-12)

Desk research from onorca.dev (home, Enterprise, and about 20 docs pages), the `stablyai/orca` repo (README, `AGENTS.md`, the
`cloud/` relay docs and contract file names, latest release notes) through the GitHub API, and the Y Combinator profile.
**Nothing was installed or run.** The desktop app is a download that needs a desktop session, and the mobile apps need a store.
Where a claim comes only from the vendor, it says so. Written beside `herdr.md`, `paseo.md` and `claude-squad.md`; the
side-by-side is in `ux-teardown-comparison.md` and the market view in `fleet-manager-landscape.md`.

**Orca is not in the earlier landscape and it is the largest product in this set by a wide margin**: 122k stars eight months
after the repo was created. The brief asked whether it is doing the same thing as cuttlefish-crew. The honest answer is in
"Overlap and difference" below: the goal overlaps a lot, the shape does not.

## The copy list, ranked

Jian's steer: do not reinvent, copy Orca's features and catch up. This list is the answer; the rest of the page is the evidence.
Effort is S (days), M (a week or two), L (more). "Fit" is against AGENTS.md and the ADRs. "Today" is what the code has now.
Cards were filed in order on board 23 (CUT-289 to CUT-302); cards that already existed are named instead of duplicated.

| # | Copy this | Card | Effort | Fit | Priority |
|---|---|---|---|---|---|
| 1 | Diff of a goal branch in the dashboard | CUT-289 (with CUT-287 stat) | M-L | Yes: read from the worktree, ADR-0048, no stored copy | High |
| 2 | Browser notifications and an unread bell for Needs you and done | CUT-290 (Web Push is CUT-121) | S-M | Yes if the text is content-free (ADR-0043) | High |
| 3 | Annotate a diff line, send it back as a steer | CUT-291 | M | Yes, uses `/steer` | High |
| 4 | Usage and reset time per backend, warning at 80% | CUT-292 | M | Yes, read-only; not hot-swap (ADR-0006, 0031) | Medium |
| 5 | Failing checks and PR comments back to the builder | CUT-293 (after CUT-242) | M | Yes, the machine's `gh`, never merges (ADR-0049) | Medium |
| 6 | Jump palette across projects and tabs | CUT-294 | S | Yes | Medium |
| 7 | Session history and resume | CUT-295 | M | Yes if derived from the journal (ADR-0004) | Medium |
| 8 | Shared directories and an include list for a goal worktree | CUT-296 | S-M | Yes; never copy `.env` (ADR-0006) | Medium |
| 9 | Needs-you lead group and an agent board across projects | CUT-284 (existing) | M | Yes | Medium |
| 10 | Schedules: cron presets, missed-run grace, precheck | CUT-297 | S-M | Yes (ADR-0051) | Low |
| 11 | Model and age on each role row | CUT-298 | S | Yes | Low |
| 12 | Continue a usage-limited builder after reset | CUT-299 | S | Yes, inside Autopilot (ADR-0044) | Low |
| 13 | Dispatch id on completion, heartbeats, gates | CUT-300 (an audit first) | S | Yes | Low |
| 14 | Add Orca to the comparison page, a Switching-from page | CUT-301 | S | Yes | Medium |
| 15 | README feature rows with loops, phone in the hero | CUT-302 | S-M | Yes | Low |

Already on the board and worth keeping in this order: CUT-242 (PR and checks status), CUT-286 (status strip), CUT-121 (Web Push
and installable app), CUT-198 (live demo over the relay, our answer to their browser), CUT-E24 (voice), CUT-E25 (worker
machines, our answer to SSH worktrees and cloud VMs), CUT-E27 (connectors, our answer to the Linear and Jira drawers).

## Gap inventory, feature by feature

What Orca has that we lack or do worse. "Today" is from the code at `main` on 2026-11-12 (route list in `fleet/server.py`,
`frontend/src/lib/`, the ADR list). "How" is from the vendor's docs, untested.

| Orca feature (docs) | How it works | Today | Effort | Fit | Priority |
|---|---|---|---|---|---|
| Diff viewer ([review/diff-viewer](https://www.onorca.dev/docs/review/diff-viewer)) | Diff pane beside the agent, inline or side by side, choice saved | A PR button and, planned, a stat (CUT-287). No diff route | M-L | Yes | High |
| Annotate AI diff ([review/annotate-ai-diff](https://www.onorca.dev/docs/review/annotate-ai-diff)) | A note on an exact changed line goes to the agent | `/steer` and a role steer card; nothing line-aware | M | Yes | High |
| Notifications and inbox ([notifications](https://www.onorca.dev/docs/notifications)) | System notice, sound, chip, bell, Dock badge, per-machine mute; phone push through a gateway | None. Web Push is CUT-121, untested | S-M | Yes, content-free | High |
| Usage and limits ([agents/usage-tracking](https://www.onorca.dev/docs/agents/usage-tracking)) | Status-bar bars per provider, 80% warning, reads local agent state | A per-run budget and limits (`budget.py`); rate-limit events arrive but are not shown | M | Yes, read-only | Medium |
| Commit, push, checks and review comments back ([review/commit-push](https://www.onorca.dev/docs/review/commit-push), [review/github](https://www.onorca.dev/docs/review/github)) | Draft commit and PR from the diff; failing checks or comments sent to an agent | Open a PR via `gh` (CUT-241); checks status is CUT-242, not built | M | Yes | Medium |
| Quick Open ([model/quick-open](https://www.onorca.dev/docs/model/quick-open)) | One shortcut jumps to any workspace or tab across repos and hosts | None | S | Yes | Medium |
| Session history ([agents/session-history](https://www.onorca.dev/docs/agents/session-history)) | List past sessions, resume as chat or as CLI | Resume exists (ADR-0034); no list | M | Yes via the journal | Medium |
| Worktree sharing and includes ([model/worktrees](https://www.onorca.dev/docs/model/worktrees)) | `worktree.sharedDirectories` symlinks; `.worktreeinclude` copies | A clean worktree and an envprep reinstall | S-M | Yes, without `.env` | Medium |
| Agent dashboard, kanban of Needs You, Working, Done, Idle ([model/agents-sessions](https://www.onorca.dev/docs/model/agents-sessions)) | Experimental board across worktrees, filters, host badge | Needs-you queue (CUT-190), ranking (CUT-189), filters (CUT-191); no board | M | Yes | Medium, CUT-284 |
| Automations ([cli/automations](https://www.onorca.dev/docs/cli/automations)) | Presets, cron or RRULE, timezone, missed-run grace, precheck, rerun | Every N min or hours or daily UTC (ADR-0051) | S-M | Yes | Low |
| Project tree with model and age | Sidebar nests project, worktree and agents | Three-column cards (CUT-285 shortens) | S | Yes | Low |
| Orchestration contract ([cli/orchestration](https://www.onorca.dev/docs/cli/orchestration)) | Run, Task, Dispatch, heartbeat, `ask`, gates, nesting to 4 | `team.py` loop over satay; audit not done | S audit | Yes | Low |
| Auto-continue after a usage limit (v1.4.256 notes) | Due chats continue in one pass, stop after five failures | Autopilot keeps a builder going; not limit-aware | S | Yes | Low |
| Native phone apps ([mobile](https://www.onorca.dev/docs/mobile)) | iOS App Store and Android APK; push, chat, terminal, source control | A PWA, never run on a real phone | L | Not planned; PWA first (ADR-0041) | Later |
| Phone drafts and queued sends survive a reconnect | Chat sends queue while the host is away | `drafts.ts` exists; queueing not checked | S | Yes | Later |
| SSH worktrees, Remote Orca Server, Cloud VM recipes ([ways-to-run](https://www.onorca.dev/docs/ways-to-run)) | Agents on a remote host; Vercel Sandbox, Fly, Modal recipes | Worker machines (CUT-E25, 1 of 7 done), sandbox is internal (ADR-0002) | L | Yes, via ADR-0057 | Planned |
| Linear, Jira, GitHub drawers; a workspace from an issue ([review/linear](https://www.onorca.dev/docs/review/linear)) | Pick an issue, branch name from it, workspace started | None; connectors are CUT-E27 | L | Yes, behind the connector ADR (CUT-217) | Planned |
| Per-worktree browser and Design Mode ([browser/overview](https://www.onorca.dev/docs/browser/overview)) | Embedded browser, click an element to copy its HTML to the agent | Artifacts canvas (ADR-0036); live demo is CUT-198 | L | Partly; not a general browser | Later |
| Dictation ([settings#voice](https://www.onorca.dev/docs/settings)) | On-device speech models | Text-to-speech only (CUT-201); voice reply is CUT-E24 | M | Browser-side only (ADR-0042) | Later |
| Agent hibernation ([agents/hibernation](https://www.onorca.dev/docs/agents/hibernation)) | Page not read | Unknown | ? | ? | Not assessed |
| Plugins, themes, rich viewers, Monaco editor | A catalog and a host API | Artifact kinds (ADR-0056) | L | No (see Do not copy) | No |
| Computer use, mobile emulator, Yolo default | Agents drive desktop apps | Never-allowed list in every mode | n/a | No | No |

**Equal or close, so not listed above:** a worktree per goal (ADR-0048) and per role (ADR-0054), a PR from the project page,
a chat and a terminal on one agent session (ADR-0044), an agent that calls the app's own tools (manager MCP, ADR-0052), device
pairing with revocable tokens (ADR-0039), schedules, status glyphs, a kill switch.

## What it is

"The AI Orchestrator for 100x builders" (README) and "Ship 100x with the agent IDE" (site). An open-source desktop app
(Electron, TypeScript) in which each task gets its own git worktree, an agent terminal or chat, a browser tab and a diff, with a
phone companion and a CLI that lets the agents drive the app themselves. It is for developers who already run Claude Code or
Codex on their own subscription and want several in parallel, and it targets solo builders first, with an Enterprise page for teams.
It says what it is not: "not a no-code tool, a model, or a git replacement." The closest named competitor is Conductor; there is a
"Migrate from Conductor" page that opens Conductor's worktrees in place and notes Conductor Cloud goes read-only on 13 November and
shuts down on 20 November 2026 (Orca's own page, which is a pitch for those users).

| Fact | Value (GitHub API and vendor pages, 2026-11-12) |
|---|---|
| Repo | `stablyai/orca`, created 2026-03-17, last push 2026-11-11 |
| Stars / forks / open issues | 122.2k / 7.7k / 8.4k (the issue count includes pull requests; I did not split them) |
| Commits | about 20.7k on the default branch in eight months |
| Releases | v1.4.256 on 2026-11-10; v1.4.249 to .256 in seven days, so about one a day |
| Weekly commit counts, last 12 weeks | 456, 712, 625, 615, 648, 824, 1167, 1499, 1622, 2448, 1781, 892 (the last is a partial week) |
| Contributors | long tail; top four have 7.6k, 3.0k, 2.3k and 2.3k commits |
| Language | TypeScript (about 159 MB of source), plus Swift and Kotlin for the phone |
| Licence | MIT (LICENSE file; the README badge agrees) |
| Company | Stably AI, YC W2022, San Francisco, 25 people per the YC page; founders Jinjing Liang (ex-Chrome) and Neil Parker (ex-Uber) |
| Other business | Stably is an AI testing product ("AI QA engineer"); Orca is the open-source side bet that got the stars |
| Price | Free and open source on macOS, Windows, Linux. Enterprise: "contact us", no prices, an "AICPA SOC 2 Readiness" label |
| Funding | Unknown. Aggregators disagree ($125k, $500k, $7.7M total) and none is a primary source |

The throughput is the first finding. About 1,500 to 2,400 commits a week from a few dozen people is far beyond what a hand-written
team produces, and the repo's own `AGENTS.md` is written for agents ("Do not add tests by default", "Run `pnpm check:changed`
before every push"). The release notes show agents' work merged by `@OrcaWin` removing "40 low-value test suites" in one PR.
This is a product built mostly by the thing it sells. I could not check how much of it is agent-written.

## Features (vendor docs, not tested)

- **Worktrees as the unit.** Created with `git worktree`; the dialog returns at once and `git fetch` and `worktree add` finish in
  the background. Branch name comes from the workspace name, a linked PR or a Linear or Jira issue. `orca.yaml` has
  `worktree.sharedDirectories` (gitignored dirs such as `node_modules` are symlinked, not copied) and a `.worktreeinclude` file
  (literal paths only) for gitignored files to copy in. "Folder workspaces" cover a directory that is not a git repo.
  Worktrees made outside Orca are hidden until you show them. The docs do not say where worktree directories live.
- **Tabs, panes, splits.** Agent, terminal, browser and diff side by side. Session restore: see Architecture.
- **Agents.** The README lists about 40 CLIs by name and "any CLI agent". The doc table counts 27. Per agent: launch from a
  combobox, a status glyph, a Restart chip.
- **Chat UI or terminal.** Structured chat for Claude, Codex, Grok, OpenCode, Pi, OMP and Antigravity; the rest, and any WSL or SSH
  session, get a terminal-backed chat. Right-click a tab to move a live session between chat and TUI; Session History offers
  "Resume in New Native Chat" or "Resume in New CLI". The docs do not name the protocol behind structured chat.
- **Agent status.** Spinner (working), amber question mark (waiting on you), check or dot (done), red dot (blocked or failed),
  grey dot (idle). Read from the terminal's OSC title and from agent hooks that Orca installs (Claude, Codex, others). An
  experimental **agent dashboard** is a kanban: Needs You, Working, Done, Idle, with search and filters.
- **Review and ship.** Diff viewer, "annotate AI diff" (a note on an exact line goes back to the agent), drafted commit messages
  and PRs, failing checks or PR comments sent back to an agent, GitHub, GitLab, Linear and Jira drawers.
- **Orchestration (experimental, off by default).** A coordinator agent creates tasks and starts workers; see Architecture.
- **Automations.** `orca automations create` with presets, cron or RRULE, a missed-run grace window and a `--precheck`
  command. Stored and scheduled on a specific host. The docs do not say whether the machine must be on.
- **Orca CLI, Computer use, mobile emulator.** "Anything you can do in Orca, your agents can do too": create worktrees, drive the
  built-in browser, tap an iOS or Android emulator, and control desktop apps. Design Mode copies a clicked element's HTML and
  styles into the agent's chat.
- **Usage and accounts.** Per-provider limit bars in the status bar with an 80% warning; Codex accounts can be hot-swapped.
- **Plugins.** A catalog of themes, editors, languages and panels, with a host API and manifest docs in the repo.
- **Dictation.** On-device speech models, vocabulary, filler-word removal.
- **Notifications.** Desktop system notification, sound and a chip when an agent goes working to idle; a bell with unread count;
  per-machine mute; phone push via a separate gateway.

## Architecture and security

**Desktop app and a background daemon.** The Electron main process owns the UI. A background daemon holds the agent processes:
after Cmd-Q or an app crash Orca reattaches to the same processes. After a reboot, OS update or power loss the processes are gone;
worktrees, tabs, splits and the last-saved scrollback restore and "you'll need to re-run the agent in each tab" (session
restore page). So it is persistence, the same level as herdr. There is no journal and no replay.

**State.** Not documented where. A release note mentions a SQLite profile copy and its WAL file, so a local SQLite store exists.
Memory is not a feature in our sense: "hooks & memory" means Orca reads the repo's `CLAUDE.md`, `AGENTS.md` and `.claude/` or
`.codex/` hooks and lets you edit them. Agent hook endpoints are written to `{userData}/agent-hooks/endpoint.env` and each hook
re-sources it, so a session that outlives an app restart still reaches the live server. There is no episodic or cross-session
memory of work done.

**Orchestration is a real protocol.** A Run (a durable namespace with a coordinator inbox) holds Tasks; a worker runs a Dispatch,
one attempt at a task. Task states: pending, ready, dispatched, completed, failed, blocked. Workers send `worker_done` once, with
`taskId` and `dispatchId` so a stale retry cannot complete the wrong dispatch; heartbeats every 1 to 60 minutes; `ask` is a
blocking question to the coordinator; a gate blocks a task until someone resolves it; nested workers to depth 4. Messages are
FIFO deliveries that are acked. Where this state lives on disk is not documented, and "durable" is the doc's word, not a
tested claim. This is the part closest to our team loop, and it is a CLI contract between agents, not a durable workflow
engine: nothing in the docs says a crashed coordinator resumes.

**Remote.** Four ways to run (ways-to-run page):

| Mode | Where agents run |
|---|---|
| Local | The same machine as the UI |
| SSH target | The remote host; UI stays local |
| Remote Orca Server | A machine running the desktop app or headless `orca serve` (Node "orcad", default port not stated); clients connect with a pairing link that is "a bearer credential"; per-client revocable tokens; Tailscale recommended; beta |
| Cloud VM (experimental) | A recipe in `orca.yaml` with create, suspend, resume, destroy scripts for Vercel Sandbox, Fly, Modal, Docker or SSH. "Orca does **not** sell managed VPS hosting" |

The Remote Server page documents no encryption, no relay and no wire protocol; it relies on Tailscale or a VPN.

**Mobile uses a hosted relay.** The `cloud/` tree of the MIT repo holds it: a director and "cells", each phone and desktop opens
an outbound WebSocket and the relay "splices frames" between them. The desktop proves itself with an X25519 key and a
Curve25519-XSalsa20-Poly1305 challenge (`host-proof-transcript.ts`), and the push gateway holds the APNs key. The relay needs a
user, profile and organisation id, and the mobile page says Orca Relay "requires sign-in", which sits oddly with the telemetry
page's "Orca has no account system". **I could not tell whether the phone-to-desktop payload is end-to-end encrypted** (the
docs say only that the relay splices frames). Ours is E2E by design (ADR-0037). The LAN and Tailscale direct modes also exist.

**Permissions: the worktree is the sandbox.** "Orca launches each agent with its full-autonomy flag already applied, such as
`--dangerously-skip-permissions` for Claude and `--yolo` for Gemini", because "the worktree acts as the sandbox" (agents and
sessions page). In structured chat, Claude and Codex offer Manual, Auto and Yolo as the default for new sessions; others
Manual or Yolo. The screenshot's composer says "Full access". The mobile docs say chat shows permissions and questions but do not
say there is a dedicated approve or deny control, and the notifications page mentions no approve action. There is no never-allowed
list, no policy tier and no manager approver anywhere in the docs I read. The Enterprise page's answer is "every agent change
lands in a worktree and a pull request", which is review after the fact.

**Telemetry.** Anonymous events to PostHog Cloud (US), opt out with a setting, `DO_NOT_TRACK=1` or `ORCA_TELEMETRY_DISABLED=1`.
Never sent: paths, repo or branch names, prompts, terminal contents, IPs.

**Credentials.** Agents use your own CLI logins. Usage tracking reads local agent state or a sign-in already on the machine, and
asks for an `opencode.ai` cookie or a GLM plan key in Settings. No broker.

**Multi-project.** Yes: a sidebar of projects, each with worktrees, each with agents, plus hosts (the screenshot shows
"Local Mac", "mac-mini" and a folder project in one tree). Quick Open jumps across every repo and host. Nothing says how it
behaves with 100 projects.

## User workflows

1. Download, add a repo, create a worktree from the base ref or a Linear issue, pick an agent, work in chat or terminal.
2. Fan out: three worktrees on three agents, a diff review per worktree, annotate lines, commit, push, PR from the same view.
3. Hand a big task to one agent that starts workers on other agents in child worktrees and reports back (the README hero).
4. Pair the phone, get a push when an agent finishes, reply "continue" or "ship it, open the PR".
5. Put a VM recipe or an SSH host behind a worktree; close the laptop; come back to running agents.
6. Schedule a nightly dependency bump or a flaky-test sweep.

## Overlap and difference

**What overlaps.** Both: many coding agents, a few CLIs behind a seam, your own subscription and logins, a phone view, SSH and
Tailscale, schedules, a worktree per goal, a hosted relay for the phone, a CLI the agent itself calls, a team of agents that
hand work to each other, and "Needs you" as a first-class state.

**What is different.** They are an **IDE for the person**: the unit is a worktree, the person drives, and everything
(editor, browser, diff, PR) is in one window so the person can review. We are a **fleet daemon for a team of roles across
many projects**: the unit is a project with a builder and others, the manager and policy handle most of it, and the person is
asked only when needed. Orca puts the human in the loop at review; we put the human in the loop at each risky action.

**So Jian is partly right.** Orca's orchestration, mobile app, remote server and automations are the same user story as our
Chat, relay, worker machines and schedules. It is not the same product: it has no durable workflow, no journal, no approval
tiers and no never-allowed list, and its default is full autonomy. If the buyer's question is "how do I run ten agents in one
window", Orca is the answer and we are not. If it is "how do I leave twenty projects running overnight and be asked only when
it matters, with a record of what happened", we are, and nobody in this set is (Paseo is nearer on remote access, herdr on
status).

## Where we are ahead (short)

Approvals (a never-allowed list in every mode, policy, a manager tier, then the person; Orca defaults to Yolo and Full access and
the docs show no tiers), a durable team workflow that a daemon resumes with an episodic journal (Orca reattaches to live
processes and restores layout; nothing replays a run), a credential broker and encrypted project secrets, and a device-pairing
model with tiers. Not an advantage until checked: the E2E relay (their payload encryption is unknown) and many projects at
once (theirs is a tree, ours is Needs-you ranking, neither measured). Everything else in the inventory is theirs.

## Do not copy (and why)

| Orca feature | Why not | Boundary |
|---|---|---|
| Agents launched with `--dangerously-skip-permissions` or `--yolo` by default, "the worktree is the sandbox" | A worktree is not a sandbox; a push, the network and writes outside it still run. Our Auto mode already trims prompts and keeps the list | AGENTS.md never-allowed rule, V4, ADR-0046 |
| Computer use, mobile emulator, agents driving desktop apps | Reaches outside the project root with no command to match against the never-allowed list | AGENTS.md never-allowed rule |
| Codex account hot-swap, OpenCode cookie and GLM key fields in Settings | Puts a model credential where an agent or the dashboard can read it | ADR-0006, ADR-0031 (credential broker) |
| Server-side dictation through an OpenAI-compatible service | A model call by cuttlefish itself. Browser-side dictation is fine and sits under CUT-E24 | ADR-0042 |
| A plugin catalog with a host API | A second extension surface beside artifact kinds; large, and nobody has asked | ADR-0036, ADR-0056 |
| PostHog usage telemetry | We send nothing home today; keep it so | ADR-0043 (the relay holds no project data) |
| A built-in editor (Monaco), file explorer and browser as an IDE | A different product; our person reviews, not edits | ADR-0044 |
| A native chat history the app owns | A parallel transcript | ADR-0004 |
| Their `orca.yaml` worktree layout | Our goal worktrees live inside the project root | ADR-0048 |

## Strategic risk

- **Reach.** 122k stars, daily releases, a YC company. Competing on "more agents in a window" is a lost fight; the copy list
  above is how we close the review and notification gap without trying to be an IDE.
- **They can add what we lead on.** Orchestration with dispatch and gates exists, an Enterprise page exists, and a permission
  policy is a small feature for a team that ships dozens of PRs a day. Our lead is a head start, not a moat.
- **Their default is a liability we can name.** Full autonomy with the worktree as the sandbox suits a solo builder and is a hard
  sell to a team ("SOC 2 Readiness", not compliance). Our buyer wants approvals and a record; keep that claim narrow and true.
- **Mobile relay overlap.** They run a hosted relay with sign-in and a push gateway. If theirs is E2E too, ours stops being a
  differentiator.
- **Star count caution.** 122k in eight months is exceptional; I did not audit it.

## What I could not verify

Everything about the running app: how a Needs You agent looks across dozens of worktrees, how it behaves at 100 projects, how the
phone handles a permission prompt, whether Orca Relay payloads are end-to-end encrypted, where state lives on disk, whether a
crashed coordinator resumes, what the protocol behind structured chat is, whether any approval policy exists beyond Manual, Auto
and Yolo, how much of the code is agent-written, real funding, whether the stars and the testimonials are organic. The vendor's
Enterprise FAQ answers did not load. No demo video was found on the home page (the README uses short looping clips). Orca has
no screenshots from Jian (`~/teardown-shots/orca` did not exist). The hero shows model names (`gpt-6-astra`, `grok-4.7`) that
are mock data, so treat it as marketing.

## Screenshots (internal comparison only)

Source: the vendor's own README, kept only for comparison; do not publish them.

| File | Source URL |
|---|---|
| `assets/orca/orca-readme-hero.jpg` | https://raw.githubusercontent.com/stablyai/orca/main/docs/assets/readme-hero.jpg |
| `assets/orca/orca-mobile-companion.jpg` | https://raw.githubusercontent.com/stablyai/orca/main/docs/assets/readme-features/mobile-companion.poster.jpg |
| `assets/orca/orca-orchestration.jpg` | https://raw.githubusercontent.com/stablyai/orca/main/docs/assets/readme-features/orchestration.poster.jpg |

The hero shows a project tree (Local Mac, mac-mini, a folder project) with worktree rows and, under each, agent rows with a
status glyph, model and age; a chat with "Started worker", "Update from worker" cards from a coordinator; a "Full access"
composer; a usage bar for three providers at the bottom; and the phone in the corner showing the same chat with "Ship it. Open the
PR." The mobile poster shows the phone over the app. The orchestration poster is the sidebar tree and worker updates.

## Sources

[onorca.dev](https://www.onorca.dev) · [docs](https://www.onorca.dev/docs) · [agents and sessions](https://www.onorca.dev/docs/model/agents-sessions) ·
[worktrees](https://www.onorca.dev/docs/model/worktrees) · [session restore](https://www.onorca.dev/docs/model/session-restore) ·
[orchestration](https://www.onorca.dev/docs/cli/orchestration) · [remote servers](https://www.onorca.dev/docs/remote-servers) ·
[ways to run](https://www.onorca.dev/docs/ways-to-run) · [mobile](https://www.onorca.dev/docs/mobile) ·
[Chat UI](https://www.onorca.dev/docs/agents/native-chat) · [automations](https://www.onorca.dev/docs/cli/automations) ·
[notifications](https://www.onorca.dev/docs/notifications) · [telemetry](https://www.onorca.dev/docs/telemetry) ·
[Enterprise](https://www.onorca.dev/enterprise) · [Migrate from Conductor](https://www.onorca.dev/docs/migrate-from-conductor) ·
[repo](https://github.com/stablyai/orca): README, `AGENTS.md`, `cloud/README.md`, latest release notes, API metadata ·
[YC profile](https://www.ycombinator.com/companies/stably-ai-orca) · aggregator funding pages (conflicting, not cited as fact).
