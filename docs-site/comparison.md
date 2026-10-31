---
icon: lucide/scale
---

# How cuttlefish-crew differs

If you are choosing a tool to run several coding agents across several projects, these are the ones we
would look at next to us. This page is short on purpose. It says what each does well, where we differ, and
where we are behind.

!!! note "Where the facts come from"
    Rows about other products are as of 2026-10-31, from their own pages and docs. We have not installed or
    run them, so a product's real behaviour may differ from what its page says. Rows about cuttlefish-crew
    are checked against this repository's code. Prices change; check each site.

## The short version

| | cuttlefish-crew | herdr | Paseo | Conductor | Claude Code Remote Control and Agent View |
|---|---|---|---|---|---|
| What it is | A daemon you run, with a web dashboard | A native desktop app and terminal UI with a local server | An open-source daemon with desktop, mobile, web and CLI clients | A Mac app, now also leading with cloud workspaces | Anthropic's phone, web and desktop window onto Claude Code sessions |
| Agents it drives | 3: kopicode, Claude Code, Codex | Any terminal agent | Many; its page lists 30+ | Claude Code and Codex | Claude Code only |
| Phone | A web app over a hosted relay or Tailscale https; no native app | None in its docs | Native iOS and Android apps | Listed as forthcoming | The Claude phone app |
| Where agents run | Your machines | Your machines, plus SSH machines | Your machines, over LAN, Tailscale, SSH or an optional relay | Your Mac, or its cloud workspaces on a paid plan | Your machine |
| Licence | Apache-2.0 | Apache-2.0 | Apache-2.0 | Closed source | Part of Anthropic's product |

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
- **Three agents in one view.** The same dashboard drives kopicode, Claude Code and Codex. Remote Control and
  Agent View cover Claude Code only.

## What the others do better today

- **Polish and breadth.** herdr and Paseo are open-source products with native apps, a one-line pitch and
  more agents than our three. Paseo's page shows 22.6k GitHub stars, a sign of a larger community than ours.
- **Native phone apps.** Paseo has iOS and Android apps, and Claude Code's Remote Control works from the
  Claude app by scanning a QR code. Ours is a web app that needs an https address (Tailscale https or the
  hosted relay) before a phone can pair, and it has been tested in Chromium and not on a real phone or in
  Safari.
- **Setup.** herdr offers a one-line install or a download. We have a one-line install script for Linux and
  WSL2 (macOS is untested), and you still add Node.js, tmux and an agent CLI yourself.
- **Git workflow.** Paseo and Conductor advertise worktree isolation and a pull-request flow, and Paseo a
  scheduled run. cuttlefish-crew has none of those today: two roles that use kopicode on the same project run
  one at a time, there is no per-agent worktree, no pull-request flow and no cron-style schedule. Autopilot
  keeps one builder working toward a goal, which is not a schedule.
- **People and cloud.** Conductor's cloud workspaces run for you. cuttlefish-crew runs only on machines you
  own, and it has one operator with one password: no accounts, roles or single sign-on.
- **Anthropic's own tools** will keep tracking Claude Code's newest features sooner than a wrapper can. If you
  only use Claude Code and a phone is all you need, Remote Control (and `claude agents`, Anthropic's
  dashboard of background sessions) may be enough.

## Which to pick

- Pick **herdr** if you live in a terminal and want a polished multiplexer for any agent.
- Pick **Paseo** if you want native phone apps and the widest agent list, and you are happy with its feature set.
- Pick **Conductor** if you work on a Mac and want worktrees and a diff-and-merge flow.
- Pick **Remote Control** if you only use Claude Code and want its phone app with no extra install.
- Pick **cuttlefish-crew** if you run several projects with more than one vendor's agent, you want each
  command's approval to be a decision you make from one place, and you want a record of what each team did.
  How much it asks is your setting, from every command to almost none. It has no scheduled runs.
