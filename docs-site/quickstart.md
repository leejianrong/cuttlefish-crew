---
icon: lucide/play
---

# Quickstart: register a project and run a crew

This walks through the path most people actually want: a dashboard showing
one or more registered projects, each running its own small crew of coding
agents.

## 1. Install, then bring up the daemon and dashboard together

On macOS or Linux, one line installs it for your user (no sudo):

```bash
curl -fsSL https://raw.githubusercontent.com/leejianrong/cuttlefish-crew/main/scripts/install.sh | sh
cuttlefish serve
```

The script installs uv if needed, clones the repository to `~/.local/share/cuttlefish/app`, installs the
`cuttlefish` command into `~/.local/bin`, builds the dashboard (Node 20.19+ or 22.12+), and runs
`cuttlefish doctor`. It names what it could not install: Node, tmux, and a coding-agent CLI (`kopicode`,
`claude` or `codex`) are yours to add. Run it again to update. `sh install.sh --check` only checks;
`sh install.sh --uninstall` removes it and leaves `~/.cuttlefish` alone.

From a checkout instead (for hacking on cuttlefish itself):

```bash
make demo
```

This builds the dashboard once, then starts `cuttlefish serve` — one
process serving both the JSON API and the dashboard itself, same origin.
It prints a URL and a token; open the URL and paste in the token. The token is
kept in `~/.cuttlefish/serve-token` (mode 0600) and reused on every start, so a
restart does not sign an open dashboard out; delete the file to rotate it. Ctrl-C or
SIGTERM stops it cleanly: running agents are told to close and are killed if they have
not gone after 5 seconds, so it exits within about 10 seconds. The daemon's log goes to this terminal and always to
`~/.cuttlefish/logs/cuttlefish.log` (rotating; `CUTTLEFISH_LOG_LEVEL=DEBUG` adds
every tool call). Each line names its project, team and role, and a failed team
start says why there. `make demo LOG=1` also copies the output to
`/tmp/cuttlefish.log` (or `LOG=<path>` for another file).

No daemon at hand yet and just want to look around? The dashboard's own
connect screen links to a sprite gallery that needs no connection at all.

## 2. Register a project

From the dashboard's portfolio view, use **Register project** — name, the
checkout it should delegate against, its roles, and (optionally) which
shell commands its team may run and a token/cost ceiling. This never
requires a terminal.

The same thing from the CLI, if you'd rather script it:

```bash
uv run cuttlefish projects add \
  --name demo \
  --root /path/to/your/checkout \
  --role builder:"careful, incremental, writes tests first" \
  --role reviewer:"terse, skeptical, flags anything undertested" \
  --allow "go test" \
  --allow "npm test"
```

`--role NAME:PERSONA` is optional per role — the persona is a persistent
voice/tone the role keeps across every task it runs, not just this one.

## 3. Give the builder a goal, or start the crew

On a project's Overview, **Give the builder a goal** is the main way to start work. Pick **Claude Code**
or **Codex**, say what you want, and cuttlefish starts that agent in the project's tmux session (section 6)
with your goal as its first message. It is passed as the tool's own first-message argument, never typed into
its terminal, so it cannot land in a permission dialog. If the builder is already running the box shows
the goal it was given and takes you to the Terminal tab; it does not type a second goal into a live session.
kopicode's interactive session cannot take a first message yet (the box says so); kopicode projects start work
with the round workflow below. `GET`/`POST /api/projects/{id}/goal` is the same thing for a script (Approve tier).

The project's **Overview** starts with a one-line **status strip**: what is running, how the session last came up
(started, reattached or relaunched, and when), Autopilot's phase, the tokens used and the time it has run, the goal's
branch with its `+added −removed` counts (marked uncommitted when the worktree has changes not yet committed), and a link
to its pull request, followed by what `gh` says about it: open, draft, merged or closed, how many checks are failing
(hover for their names), and whether changes were requested. It shows nothing for a project that has never been given work. The counts come from
`GET /api/projects/{id}/goal/diff/stat`, the diff's numbers without the patch, so it is cheap to poll (View tier; the
same refusals as the [diff](branches-and-pull-requests.md#see-what-the-branch-changed)). Tokens and time need Autopilot or
a team; a terminal-command project has neither, since cuttlefish reads nothing from that tool. Under the project's name
the folder is one line, with the directory shortened before the folder name; press it to see the whole path.

Below it, the Overview opens with the builder's goal and a progress ring, then **While you were away** (what happened
since you last looked: commits, the last test run, context refreshes, files left for you, the builder's own recap, what
waits on you; it is skipped if you were here in the last ten minutes) and a **timeline** of Autopilot's steps, Needs you
requests, commits, test runs, context refreshes and the images, videos and designs the builder made. None of it is stored:
the daemon builds it on each read from the journal, the artifacts folder, `git log` (run with the agents' filtered
environment, with git told not to run any helper the repository's config names) and the builder's transcript. A test run is
recognised from the builder's own test command and the `N passed` and `N failed` in its output, so a runner that prints
differently does not appear. `GET /api/projects/{id}/overview?since=<ISO time>` is the same for a script.

What a builder leaves in `.cuttlefish/artifacts/` shows on the Chat tab's canvas and the Artifacts tab: markdown, images,
`mp4` or `webm` video, and self-contained `.html` designs. A design runs in a sandbox with scripts on and everything else
off (no network, no access to the dashboard, your files or its storage), so one that loads a library or font from a CDN shows
without it; inline style, script and `data:` images work.

The project's **Chat** tab shows the builder's conversation as a thread, read live from Claude Code's own
session record (`~/.claude/projects/<folder>/<session>.jsonl`; nothing is stored a second time). Tool calls are
folded into one line each run ("Ran 4 commands, edited 2 files") that opens to the calls and their output, a context
compaction is a divider, and a permission the builder is waiting on is answered inline, as under Needs you.
Under the thread a box types to the builder: the text is pasted into the session as one bracketed paste and then
Enter, so a line break in your message is not an Enter and the terminal and the chat stay one conversation. A message to a
builder that is working is queued by Claude Code and read at its next step. The box refuses while a dialog is open (a
permission waiting here or in the terminal), because a dialog would read the message as its answer; whether one is open
comes from the session's hooks and the last lines of its screen. It needs the Terminal level on a paired device, as typing
in the terminal does, and the control characters in a message are removed so it cannot end its own paste early.
A Codex session is shown there too, read from Codex's own session record (`~/.codex/sessions`, found by the project's folder);
it is read-only, so type to a Codex builder in the Terminal tab. A kopicode session is shown there too, from `<project>/.kopicode/sessions/*/events.jsonl`, also read-only. A device paired through the relay needs the Approve level to read it, because it shows what the
agent's tools printed. `GET /api/projects/{id}/chat?after=<cursor>` is the same thing for a script, and `POST /api/projects/{id}/chat` with `{"text": ...}` sends a message.

A builder started in a folder Claude Code has not seen before first shows Claude's own "trust this folder" dialog in the
terminal. The dashboard sees it as a dialog: the message box is disabled and a send is refused, so answer it in the Terminal tab.
The exception is a goal's own worktree that cuttlefish made (see [Branches and pull requests](branches-and-pull-requests.md)):
cuttlefish answers "Yes, I trust this folder" there so an unattended run is not stopped.

**Autopilot** is on by default for a builder given a goal from the dashboard. The builder is told to plan first: to write
a checklist (`- [ ] step`) to `.cuttlefish/artifacts/plan.md` and wait. The Chat tab shows the plan as a card with **Approve
the plan**, and a rail with the goal, the checklist and the run's figures. Once you approve, the builder works through the
steps ticking `- [x]`, and whenever it stops with steps left the daemon sends it "continue", until the plan is done, a limit is
reached (24 hours, 5 million tokens, 200 nudges; set the first two on the goal card), or it is **stuck** (three nudges in a
row with no step ticked). It asks you only for a permission, or if it is blocked and says so. **Pause** and **Stop
Autopilot** are in the rail (and above the thread on a phone); they stop the nudging, not the agent mid-step. Everything
Autopilot sends goes through the same guarded path as a message you type, so it never types into a permission dialog. Its
state survives a daemon restart (it is in the journal), but a builder that went idle while the daemon was down waits for
its next stop or for **Nudge now**. `GET /api/projects/{id}/autopilot` and `POST .../autopilot/{approve|pause|resume|stop|nudge}`
are the same for a script; the goal route takes `"autopilot": true` or `{"hours": 8, "tokens": 2000000, "nudges": 100}`.

**A usage limit.** If a Claude Code builder stops because its plan's usage limit was hit, Autopilot does not nudge it into
the wall. It shows **Waiting for the usage limit** with the time it will continue (`Will continue at 2026-11-12 11:01 UTC`,
a minute after the reset, in the rail, the goal card and the timeline), sends the one usual "continue" then, and tries again
after another limit up to five times in a row before it gives up and says so. It happens only for an Autopilot you approved,
only when the stop was the usage limit (Claude Code's `StopFailure` hook, error `rate_limit`), and never past Autopilot's own
time, token and nudge limits. **Pause**, **Stop Autopilot**, **Carry on now** and the kill switch end the wait, and a message
you send the builder ends it too. If the stop does not say when the limit resets and the usage card has not seen it either,
nothing is guessed: the rail says the reset time is not known and the builder waits for you. The switch **Continue after a
usage limit resets** on the goal card is per project and on by default, because Autopilot is already your yes to unattended
continuing and the wait is bounded and visible; turn it off and a limited builder just stays stopped
(`PATCH /api/projects/{id}/continue-after-reset`, Admin). Only Claude Code reports the stop; Codex and kopicode sessions have no
such hook. A session started before this version needs a restart to pick the new hook up. The wait survives a daemon restart.

**Stop everything.** A **Stop all agents** button at the top of the Projects page, and **Stop everything here** on each
project page, end every running builder at once and ask a running team to stop. Each is two taps (the second is **Stop now**),
and both work from a paired phone, because they need only the Approve tier, the same as Stop. A builder's tmux session is
gone in well under ten seconds; a team ends when its current round does (a Codex agent is interrupted at once). Each press
is journaled under the project's agent session (`KillSwitchPressed`). A script can use `POST /api/projects/{id}/kill` and
`POST /api/kill-all`.

When the builder needs a choice only you can make, it is told to write `.cuttlefish/artifacts/decision.json` (a
`question`, two to four `options`, the `recommended` one and the `cost_of_waiting`) and stop. On that stop Autopilot raises a
**Decision** card under Needs you, on the phone as on the desktop, with the recommended option filled in and what stalls
meanwhile, and does not nudge while it waits. Pick an option and it is typed to the builder as a message. The card and who
answered are in the journal.

By default the card waits with no timer. On the project's Permissions tab, **Decisions the builder asks you** sets a wait
(15 minutes to 24 hours; `PATCH /api/projects/{id}/decision-wait` with `{"decision_wait_minutes": 60}` is the Admin-tier
call). When it runs out the recommended option is taken, the card counts down to that, the journal records the answer as
chosen by `timeout`, the history says "Chosen automatically after the wait", and the builder is told nobody answered. An
option that deletes or overwrites work is never taken for you: the builder lists such options as `destructive`, and
wording like "delete", "drop" or "reset" is treated the same way, so that card keeps waiting.

The round and team workflow is under **Advanced: run a team of roles** on the same page. Click **Start team** there, or:

```bash
uv run cuttlefish run-team \
  --role builder:"implement the login form" \
  --role reviewer:"review the last commit for style issues"
```

Both roles start together (`satay.gather`). With two kopicode-backed roles in a git project, each works in its own
worktree and the results are merged into a `cuttlefish/team-<id>` branch, leaving your checkout alone; in a folder that
is not a git repository they share it and are dispatched one at a time, because kopicode locks a working tree per
session. Watch progress live in the dashboard — each role renders as a
small animated sprite whose pose reflects its status.

## 4. Steer it, or require your sign-off before it finalizes

Redirect a still-running role from the dashboard's steer panel, or:

```bash
uv run cuttlefish steer <task-id> "actually use .dockerignore instead" --role builder
```

A steering message takes effect at the boundary between delegation rounds,
not mid-flight — it waits for whatever round is currently running to
finish first. The exception is a Codex role steered from the dashboard: the
message goes into its running turn at once
([ADR-0033](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0033-a-codex-turn-in-flight-takes-a-steer-through-turn-steer.md)).
Otherwise this is a deliberate limit
([ADR-0008](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0008-steering-is-a-round-boundary-redirect-not-a-mid-flight-interrupt.md)),
not a missing feature.

For a formal review gate instead of an optional redirect — a round never
finalizes until you decide — start the project (or task) with
`--require-approval`, then approve or reject from the dashboard's panel, or:

```bash
uv run cuttlefish approve <task-id> --role builder                        # finalize it
uv run cuttlefish approve <task-id> --role builder --reject "use .dockerignore instead"  # one more round
```

## 5. Answer an agent that needs you

In **Ask first** and **Standard**, a kopicode agent that wants to run a command nothing approves
stops and waits. It shows up under **Needs you** (a tab on the project, and a destination in the
rail with a count) with the command, why it stopped and a countdown. Choose **Allow once**,
**Always allow** (the start of the command, which applies to the running team at once and is saved
to the project's own commands for later starts), or **Deny**. If you do nothing, the request denies
itself when `CUTTLEFISH_REQUEST_WINDOW` runs out (ten minutes by default) and the agent carries on
with a refusal. Restarting the daemon abandons a waiting request; it is not asked again. A Claude Code session's held permission is the exception to being "not asked again": its hook script asks the restarted daemon again for up to a minute, so a card that was abandoned comes back as a new one (the old one stays in History as "may still be open in the terminal"); a session that outlasts that minute is answered in the terminal.

When an agent asks you a question with kopicode's `ask` tool (kopicode v0.4.0 or later), it is a
**Question** card with a text box: **Send answer** gives the agent your words, **Decline** (or no
answer in time) tells it nobody is there and it carries on.

every backend can pause in the daemon: Codex over `codex app-server` and Claude Code over stream-json
(the defaults; with `CUTTLEFISH_CODEX_TRANSPORT=exec`, `CUTTLEFISH_CLAUDE_CODE_TRANSPORT=print`, or inside
a sandbox provider they refuse the command and say so in the activity log). A team started with `cuttlefish run` or `run-team` has no inbox, so it refuses too. The same
requests are available over HTTP (see the [CLI reference](cli-reference.md)).

A **Stuck** card is different: it has no buttons. A kopicode agent whose shell commands fail five
times in a row because a tool or package is missing (`No module named`, `command not found`, ...)
is stopped, and the card shows its last failing command. Nothing is waiting for an answer. Fix the
environment, then steer that role from the project page; the card ends when you do.
`CUTTLEFISH_STUCK_THRESHOLD` changes the five, and `0` turns the check off.

**The bell.** The bell in the top bar counts what is new since you last opened it: a request that started waiting,
or a project that finished. Opening it lists them and links to Needs you. It can also send a desktop notification
("A project needs you", "A project finished"), which names no project and no command. Turn that on from the bell (the
browser asks then, not before); **Mute on this browser** turns it off again. These notices come from the open dashboard tab:
with no dashboard tab open, or with notifications blocked for the site, nothing reaches you, and the bell says so. A tab you are
looking at gets the bell only. Pushing to a closed browser is not built.

**Jump to anything.** Press Ctrl-K (Cmd-K on a Mac), or the search button beside the bell, and type: a project, a tab
("billing team"), a screen, or a project that needs you. Arrow keys move, Enter goes, Esc closes.

## 6. Keep an agent running in tmux

The **Agent session** card on a project's Overview (also the MCP tools `get_agent_session`,
`start_agent_session` and `stop_agent_session`) starts the project's agent, whichever of
`claude`, `codex` or `kopicode` the project uses, inside tmux. Needs `tmux` installed.

- Close the browser, or restart `cuttlefish serve`: the agent keeps running, and the card says
  it **reattached**.
- Reboot: tmux is gone. The next `cuttlefish serve` starts the agent again with the tool's own
  resume command, and the card says it was **relaunched**. Resume is `claude --resume <id>`,
  `codex resume --last` or `kopicode -resume <id>`.
- **Stop session** ends it on purpose and it is not brought back at the next start; **Resume
  session** picks the same conversation up.

The project's **Terminal** tab is that tmux session in the browser: type to the agent, answer its permission
prompts, watch it work. It uses the same sign-in as the rest of the dashboard (the token on loopback, the
password session otherwise), has a key row for a phone keyboard (Esc, Tab, Ctrl-C, arrows, Enter), and closing
it leaves the agent running. On a phone the terminal takes the height that is left between the header and
the bottom bar, with the key row right under it, and the page title carries the Needs you count, like
`(2) cuttlefish-crew`. With no session running it offers to start one. The terminal is dark in both app
themes by default, with a Match app / Dark / Light switch that your browser remembers.

Beside it, the **Shell** switch opens a plain shell in the project folder, with no agent needed. It is a
second tmux session (`cf-shell-<project>`), so it keeps running when you close the tab, and it is started the first
time you open it. It has the same filtered environment as the agents and no project secrets, so a variable
you need there goes in `CUTTLEFISH_AGENT_ENV_PASSTHROUGH`. It needs the same Terminal rights as the agent's terminal.

### Any other terminal agent

A project can name a command to run in its tmux session instead of Claude Code, Codex or kopicode, so a
tool cuttlefish has no integration for (aider, gemini-cli, your own script) still stays up and is reachable from
the Terminal tab:

```bash
uv run cuttlefish projects add --name demo --root /path/to/checkout --terminal-command 'aider'
```

To change it on an existing project, `PATCH /api/projects/<id>/terminal-command` with
`{"terminal_command": "aider"}` (or `null` to clear it); it needs the Admin tier, since it is a shell command, and
applies the next time the session starts. The command runs in the project root with the same filtered environment
as the agents (no model keys, no project secrets; add what it needs to `CUTTLEFISH_AGENT_ENV_PASSTHROUGH`).

cuttlefish does not know this tool, so there is **no live status, no chat, no goal and no Needs you cards** for it.
Anything it asks appears only in the Terminal tab, and the project page says so. If it exits, the next start runs it
again from the start; there is no resume.
([ADR-0047](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0047-a-project-can-run-any-terminal-command-in-its-tmux-session-with-no-status-integration.md)).

## A branch per goal, pull requests, schedules

Three things build on the goal box, each with its own page:

- **Its own branch** runs a goal in a new git worktree and branch, and **Open a pull request** turns that branch into
  a GitHub pull request with your own `gh` login: [branches and pull requests](branches-and-pull-requests.md).
- **Scheduled goals** ask the builder for a goal on a repeat while you are away: [schedules](schedules.md).
- A manager agent can watch every project over MCP: [manager tools](manager-tools.md).

## Artifacts

Agents are told to write results as markdown files under `.cuttlefish/artifacts/` in the project, and the
project's **Artifacts** tab shows them: markdown, and images (png, jpg, gif, webp, svg), newest first, refreshed
every few seconds. Other files are listed by name. The dashboard renders the markdown itself and drops raw HTML,
`javascript:` links and remote images, so nothing in a file runs. Every round's task carries the instruction, as
does a Claude Code session started in tmux; a Codex or kopicode session in tmux is not told yet. HTML
artifacts are a later slice
([ADR-0036](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0036-agents-leave-markdown-and-images-in-an-artifacts-folder-and-the-dashboard-renders-them-itself.md)).

A Claude Code session in tmux asks for permission in its own terminal **and** under Needs you: cuttlefish starts it
with a hook that raises a card, so you can allow or deny from the dashboard or a phone, and answering in the terminal
clears the card. A command on the project's list runs without asking and a never-allowed one is refused without a card,
as for a team. A session started in Auto mode never asks, and the Agent session card says so. Changing a project's mode
applies to the next session you start. Codex and kopicode sessions still ask only in their terminals
([ADR-0034](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0034-the-project-agent-runs-in-tmux-and-is-relaunched-with-its-own-resume-command.md),
[ADR-0040](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0040-a-claude-code-session-in-tmux-raises-needs-you-cards-through-a-held-permission-hook.md)).

## 7. Reach it from another device

```bash
cuttlefish serve --tailscale
```

Needs `CUTTLEFISH_SERVE_PASSWORD` set (a non-loopback bind switches from
the loopback default's shared static token to real password/session auth).
Opening the printed URL directly on your tailnet needs no extra flag. Keep
it running across crashes and reboots with `make install-systemd-service` —
it reviews and prints the exact next commands, never starts anything on its
own.

If the daemon restarts while the dashboard is open, it shows **Sign in again** and carries on where you
were; a session you keep using stays signed in (12 hours idle, seven days at most).

Every project card opens with one state and a sentence saying why. The state comes from the project's journal and the
requests waiting on it, checked in this order:

| State | When |
|---|---|
| Waiting | A request needs you, or a round ended and the review gate wants you ("builder is waiting for your review"). |
| Idle | No role has started yet, or you stopped the team. |
| Stuck | A role failed, or a role that should be moving has written nothing to the journal for 3 hours ("No progress for 5 h"). A round's own time limit is 2 hours, so a quieter project than that has lost its round. |
| Working | A role is mid-round or between rounds, or a team is running, and the journal moved in the last 3 hours. |
| Done | Every role that started has finished. |

Projects is ordered by that state: waiting first, then stuck, then working, then done and idle, each group with the
most recently active first. **Pin to top** on a card keeps it above the rest whatever its state; pins live in this
browser only. The order only moves when a project's state does.

A project whose last goal has its own branch shows one more line on its card: how many files the branch changes with
`+added −removed` (marked uncommitted while the worktree has changes not yet committed), and its pull request, open,
merged or closed, with whether its checks are failing, passing or running. A failing check is coloured as a warning.
The daemon keeps these in a cache that one background worker refreshes about once a minute (the pull request, which
needs `gh`, every five minutes), so a poll of the project list never runs git or `gh`: after a start the figures appear
on the second poll, and a project with no goal branch, or placed on a worker, shows nothing. The list leaves out the
default command presets of each project to make room (they are on the project's own page).

Under each role on a card is one line: the backend it runs on, the model, and how long ago it last did anything
("kopicode · llama3 · 4 min ago"). The model shows only where cuttlefish sets one itself (`CUTTLEFISH_KOPICODE_MODEL`
with a provider URL, `CUTTLEFISH_CODEX_MODEL`); a tool running on its own default, which is every Claude Code role, shows
the backend alone, because cuttlefish cannot read what the tool picked.

Projects that are waiting for you sit in a **Needs you** group at the top instead of among the cards: one row each,
with the title of the request it is waiting on (or that a review is wanted). Five show; **Show all** reveals the rest.

With more than a handful of projects, use the bar above the cards. **Tags** on a card takes free-form labels separated
by commas (`client:acme, rust`; up to 10 per project, 24 characters each) and saves them on the daemon, so every
browser sees them. The state chips show only projects in those states (any of them); the tag chips show only projects
that carry every selected tag. **Group by** splits the list by each project's first tag (projects with none go under
Untagged) or by state; a group's heading collapses it, and the choice is remembered in this browser. **Clear filters**
puts everything back.

The top bar's account menu (the round icon at the top right) holds the theme (match system, light or dark), the
sprite gallery and **Disconnect**. The bottom bar on a phone has the four places that span every project: Needs you,
Projects, Role library and Shared secrets. A project's own Team, Permissions and Secrets are under its **Settings** tab.

To try this from a phone first, with no model credit and none of your own projects, run `make phone` from a
checkout. It checks that Tailscale and tmux are ready, then serves one sample project on the Tailscale address
(port 8431, or `PORT=n`) with a fake agent in tmux, and prints the URL and a scratch password. State lives in
`/tmp/cuttlefish-phone`; `make phone RESET=1` starts over.

To pair a phone, run `make phone HTTPS=1` instead. It starts the relay and the daemon on loopback, shows the two
`tailscale serve` commands and runs them only if you say yes (with `sudo` if Tailscale asks for root), prints the https
address and the password, and offers to remove the two entries when you stop it. It needs HTTPS turned on for the tailnet
([details](cli-reference.md#pairing-over-tailscale-https)).

### Preview: pair a phone through a relay

To use the encrypted relay path on one machine, run `cuttlefish relay` in one terminal and
`cuttlefish serve --relay ws://127.0.0.1:8450` in another. In the dashboard open the account menu, then **Devices**, make a
pairing link and open it on the device. The page it opens must be https or `http://localhost`: a browser does not allow
this encryption on a plain-http address, so a Tailscale `http://100.x.y.z` address signs in with the password but cannot pair
(see the [CLI reference](cli-reference.md#cuttlefish-relay)); to pair a phone over Tailscale, use
[`--tailscale-https`](cli-reference.md#pairing-over-tailscale-https). Both screens show a six-digit code; confirm on the PC if they match. A new device
may read, answer Needs you, steer a running team, start and stop; you give it the terminal or admin rights under Devices. Details and
limits are in the [CLI reference](cli-reference.md#cuttlefish-relay). The hosted, multi-user relay (accounts, rooms, the page
served from its own address) is described there too.

See [CLI reference](cli-reference.md) for every command and flag, and
[Architecture](architecture.md) for how the pieces fit together.
