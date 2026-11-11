---
icon: lucide/scale
---

# How cuttlefish-crew differs

If you are choosing a tool to run several coding agents across several projects, these are the ones we
would look at next to us. This page is short on purpose. It says what each does well, where we differ, and
where we are behind.

!!! note "Where the facts come from"
    Rows about other products are as of 2026-10-31 (Orca: 2026-11-12), from their own pages and docs. We have not installed or
    run them, so a product's real behaviour may differ from what its page says. Rows about cuttlefish-crew
    are checked against this repository's code. Prices change; check each site.

## The short version

| | cuttlefish-crew | Orca | herdr | Paseo | Conductor | Claude Code Remote Control and Agent View |
|---|---|---|---|---|---|---|
| What it is | A daemon you run, with a web dashboard | An open-source desktop app: each task gets a git worktree with an agent, a browser and a diff in one window, plus a phone companion | A native desktop app and terminal UI with a local server | An open-source daemon with desktop, mobile, web and CLI clients | A Mac app, now also leading with cloud workspaces | Anthropic's phone, web and desktop window onto Claude Code sessions |
| Agents it drives | 3: kopicode, Claude Code, Codex | Its README names about 40 CLIs | Any terminal agent | Many; its page lists 30+ | Claude Code and Codex | Claude Code only |
| Phone | A web app over a hosted relay or Tailscale https; no native app | iOS and Android apps, with push, through a hosted relay that needs sign-in | None in its docs | Native iOS and Android apps | Listed as forthcoming | The Claude phone app |
| Where agents run | Your machines | Your machine, SSH hosts, a remote Orca server, or cloud VM recipes you write (it does not sell hosting) | Your machines, plus SSH machines | Your machines, over LAN, Tailscale, SSH or an optional relay | Your Mac, or its cloud workspaces on a paid plan | Your machine |
| Licence | Apache-2.0 | MIT | Apache-2.0 | Apache-2.0 | Closed source | Part of Anthropic's product |

## Where cuttlefish-crew is different

- **Approving a command is a first-class screen.** An agent that wants to run something not on the project's
  list pauses, and the request lands under Needs you with **Allow once**, **Always allow** or **Deny**, from
  the dashboard or a paired phone. You pick how much asks: Ask first (every shell command waits), Standard
  (a list of safe commands runs, others ask) or Auto (everything except the never-allowed list), and a role
  can be read-only. The never-allowed list (`sudo`, a forced push, a download piped into a shell, writes
  outside the project root) holds in every mode. See [permission modes](cli-reference.md#permission-modes).
- **A team survives a crash.** Each team runs as a durable [satay](https://github.com/leejianrong/satay-runtime)
  workflow, and a restarted fleet daemon resumes unfinished teams. The one-shot `cuttlefish run` does not
  resume on its own.
- **One journal of what happened.** Everything a person reads back is derived from an episodic journal, with
  secrets redacted when they are written.
- **Credential handling.** Project secrets are stored encrypted. With `CUTTLEFISH_CREDENTIAL_BROKER=1` the
  daemon keeps an agent's model API key and gives the agent a short-lived token instead.
- **You decide before a command runs, not after.** Orca launches each agent with its full-autonomy flag already on
  (`--dangerously-skip-permissions` for Claude, `--yolo` for Gemini), on the reasoning that the worktree is the
  sandbox, and its docs show review of the diff and a pull request as the check. A worktree does not stop a push or a
  network call. Here the default asks, and the never-allowed list holds even in Auto. Its docs show no
  never-allowed list, policy tier or approver agent; we have not run it.
- **A record you can replay.** Orca reattaches to live agent processes after a restart and restores its layout; its
  docs describe no journal of what each agent did. Ours is derived from one.
- **Three agents in one view.** The same dashboard drives kopicode, Claude Code and Codex. Remote Control and
  Agent View cover Claude Code only.

## What the others do better today

- **Reach and pace.** Orca's repository shows 122k GitHub stars eight months after it was created and a release
  most days. We cannot compete on that, and we do not try to put an editor, a browser and a terminal in one window.
- **The review window.** Orca puts the diff, an editor, a built-in browser and its GitHub, GitLab, Linear and Jira
  panels beside the agent, and sends failing checks and pull-request comments back to it. We show a goal branch's
  diff and let you send a note on a line to the builder, but we have no editor or browser, no checks or review comments
  back, and no issue-tracker panels. It also lists past sessions to resume and shows each provider's usage against its
  limit; we do not.
- **Alerts and a phone app.** Orca sends a system notification and a push to its phone app. Our bell and desktop
  notices work only while a dashboard tab is open, and our phone is a web app.
- **Polish and breadth.** herdr and Paseo are open-source products with native apps, a one-line pitch and
  more agents than our three. Paseo's page shows 22.6k GitHub stars, a sign of a larger community than ours.
- **Native phone apps.** Paseo has iOS and Android apps, and Claude Code's Remote Control works from the
  Claude app by scanning a QR code. Ours is a web app that needs an https address (Tailscale https or the
  hosted relay) before a phone can pair, and it has been tested in Chromium and not on a real phone or in
  Safari.
- **Setup.** herdr offers a one-line install or a download. We have a one-line install script for Linux and
  WSL2 (macOS is untested), and you still add Node.js, tmux and an agent CLI yourself.
- **Git workflow.** Orca, Paseo and Conductor advertise worktree isolation and a pull-request flow, and Paseo and
  Orca a scheduled run. cuttlefish-crew has a worktree and branch per goal (opt-in), a diff of it in the dashboard and a button that opens a GitHub pull
  request through your own `gh` login, but no merge, no PR status and no cron-style schedule; two kopicode roles on a git
  project each get a worktree and run together, merged into a team branch. Autopilot
  keeps one builder working toward a goal, which is not a schedule.
- **People and cloud.** Conductor's cloud workspaces run for you. cuttlefish-crew runs only on machines you
  own, and it has one operator with one password: no accounts, roles or single sign-on.
- **Anthropic's own tools** will keep tracking Claude Code's newest features sooner than a wrapper can. If you
  only use Claude Code and a phone is all you need, Remote Control (and `claude agents`, Anthropic's
  dashboard of background sessions) may be enough.

## Which to pick

- Pick **Orca** if you want one desktop window with the editor, browser, diff and pull request around each agent and
  you are happy for agents to run with full autonomy; it is the most active project here.
- Pick **herdr** if you live in a terminal and want a polished multiplexer for any agent.
- Pick **Paseo** if you want native phone apps and the widest agent list, and you are happy with its feature set.
- Pick **Conductor** if you work on a Mac and want worktrees and a diff-and-merge flow.
- Pick **Remote Control** if you only use Claude Code and want its phone app with no extra install.
- Pick **cuttlefish-crew** if you run several projects with more than one vendor's agent, you want each
  command's approval to be a decision you make from one place, and you want a record of what each team did.
  How much it asks is your setting, from every command to almost none. Its schedules repeat on an interval or daily in UTC, with none of Orca's cron presets.

Coming from Orca, Conductor or herdr? See [Switching from another tool](switching.md).
