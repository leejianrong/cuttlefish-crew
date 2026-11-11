---
icon: lucide/arrow-right-left
---

# Switching from another tool

You run coding agents in Orca, Conductor or herdr and want to try cuttlefish-crew next to them, or instead.
This page says what carries over, what does not, and which screen does the job you are used to. Facts about the
other tools are from their own pages as of 2026-11-12; we have not run them, so check a detail before you rely on it.
For a side-by-side of the products, read [How we differ](comparison.md).

## What carries over

Nothing is imported. cuttlefish-crew has no importer for another tool's workspaces, and it does not read their
settings. (Orca has a "Migrate from Conductor" page that opens Conductor's worktrees in place; we have no
equivalent.) What you do keep:

- **Your repositories and your logins.** The agent is the CLI you already use (`claude`, `codex` or
  [kopicode](https://github.com/leejianrong/kopicode)) with its own login. cuttlefish makes no model call of its own.
- **Your branches.** Work is plain git. A goal's branch and pull request are on your machine and your remote, and they
  stay there if you stop using cuttlefish.
- **Your `CLAUDE.md` or `AGENTS.md`.** The agent reads them as it always did.

## Start in three steps

1. [Install](quickstart.md) and run `cuttlefish serve`.
2. Register the **main checkout** of each repository with **Add project** (or
   `cuttlefish projects add --name myapp --root ~/code/myapp`). Do not register a worktree another tool made: a
   cuttlefish goal makes its own worktree under `<project>/.cuttlefish/worktrees/<slug>`, on a branch named
   `cuttlefish/<slug>`, when you switch on **Its own branch**. Worktrees from other tools are left alone.
3. Give the builder a goal on the project page. Your old worktrees and branches stay where they are.

## Where each thing is

| In the other tool | In cuttlefish-crew |
|---|---|
| A worktree per task (Orca, Conductor) | **Its own branch** on a goal: a worktree and branch per goal, kept after the goal ends. A team of kopicode roles gets one worktree per role, merged into a team branch |
| An agent tab, chat or terminal | The project's **Chat** and **Terminal** tabs, on one agent session in tmux |
| Status glyphs, a Needs You column (Orca's agent dashboard, herdr's inbox) | A state word on every project (Working, Waiting, Done, Stuck, Idle) and a **Needs you** group at the top of Projects, one row per project that is waiting, with the reason |
| Allow or deny a command | **Needs you**: Allow once, Always allow or Deny, from the dashboard or a paired phone. How much asks is a project setting |
| A diff pane and line comments | The **Changes** card shows the goal branch's diff; press a line number to send a note on that line to the builder |
| Open a pull request | **Open a pull request** on the project, through your own `gh` login. It never merges |
| Quick Open (Orca) | **Ctrl-K** (Cmd-K), or the search button in the top bar on a phone: jump to a project, a tab or a project that needs you |
| System notification and a bell | The bell in the top bar and optional desktop notices, which work only while a dashboard tab is open |
| Running agents on another machine over SSH | A **worker machine**: `cuttlefish worker join` on the second computer, then place a project on it. A project can move between machines, and Devices shows each machine's load |
| A phone app | A web app you pair over Tailscale https or the hosted relay |
| Automations or a scheduled run | A [schedule](schedules.md) on an interval or daily in UTC |
| A terminal multiplexer for any agent (herdr) | The Terminal tab, for any terminal agent you register with `--terminal-command`; it has no live status or Needs you cards |

## What you will miss

- **An editor, a browser and a window around the agent.** There is no built-in editor or browser. You review, you do
  not edit here.
- **Issue-tracker panels, failing checks and review comments sent back to the agent.** Not built.
- **A native phone app, push to a closed browser, and a list of past sessions to resume.** Not built.
- **Usage against each provider's limit.** cuttlefish shows the tokens a run used, not what is left on your plan.
- **Orca's cron presets** and its other agent-driven features, such as an agent that starts workers on other agents.

## What you will notice

- **It asks first by default.** A command not on the project's list pauses the agent until you answer. The
  never-allowed list (`sudo`, a forced push, a download piped into a shell, writes outside the project) holds in
  every mode, Auto included. If you want fewer questions, change the project's mode; see
  [permission modes](cli-reference.md#permission-modes).
- **It keeps going without you.** The team is a durable workflow: restart the fleet daemon and unfinished teams resume.
- **Everything lands in one journal**, with secrets redacted when they are written, which is what the dashboard reads back.
