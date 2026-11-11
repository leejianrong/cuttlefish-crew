# Claude Squad teardown (CUT-228, 2026-11-12)

Desk research from the repo README and source layout (`smtg-ai/claude-squad`, via the GitHub API). **Nothing was installed or
run.** The README's install is `curl | bash`, which this spike does not run. The shortest of the three teardowns because the
product is small and old.

## What it is

A terminal UI (Go) that runs several coding agents in parallel, each in its own tmux session and git worktree. Command `cs`.
Licence **AGPL-3.0** (the only AGPL project in this set; there is also a `CLA.md`). 8.8k stars, v1.0.20 on 2026-08-20, last push
the same day, created 2025-03. Free. The README's headline: "manage multiple Claude Code, Codex, Gemini (and other local agents
including Aider) in separate workspaces".

## Features

- Left list of **instances** (title, branch, `+added,-removed` diff stat, a status dot); right pane toggles **Preview** (the
  agent's live terminal) and **Diff** with `tab`.
- Keys: `n` new, `N` new with a prompt, `D` kill, `enter/o` attach (detach with `ctrl-q`), `s` commit and push the branch
  ("submit PR"), `c` checkout (commit and pause the session), `r` resume a paused one.
- **Isolation**: a worktree and branch per session, so no file conflicts.
- **Profiles**: named launch commands in `~/.claude-squad/config.json` (`claude`, `codex`, `aider --model ...`), picked with
  arrow keys when you create a session. The default program is `claude`. Any CLI that runs in a terminal works.
- **Auto-yes** (`-y`, experimental): accepts prompts for Claude Code and Aider automatically; the README says "for claude code &
  aider".
- Prerequisites: tmux and `gh`.

## Architecture (from the source tree)

- tmux holds each agent's terminal; `session/tmux` drives it, `session/git` makes the worktree.
- State: `~/.claude-squad/` JSON; an instance record stores title, path, branch, status, size, `auto_yes`, program, worktree
  (repo path, worktree path, session name, branch, base commit SHA) and diff stats.
- A background **daemon** (`daemon/`) polls every instance and applies auto-yes while the TUI is closed; the main process kills
  it when you reopen the TUI. That is its only job; it is not a task runner.
- No server, no network protocol, no remote access, no phone. Remote use is SSH plus the TUI.

## User workflow

`cs` in a repo; `n`; type a prompt; watch Preview; `tab` to Diff; `s` to push when it looks right; `c` to pause. The mental
model is a list of branches with an agent behind each.

## Compared with the other two

It is the simplest model of the three and the closest to a plain tmux setup. It has no status beyond a dot, no cross-project view
(one repo per `cs` run), no permission routing beyond auto-yes, no remote client, and no durable state beyond the JSON file and
tmux itself. What it does well, and what we also offer in part: worktree-per-session and a fast Preview/Diff toggle with a PR
push key. The README names no crash recovery. Its AGPL matters if anyone considers copying code: ours is Apache-2.0, so do not.

## Screenshots (internal comparison only)

| File | Source URL |
|---|---|
| `assets/teardown/claude-squad-screenshot.png` | https://raw.githubusercontent.com/smtg-ai/claude-squad/main/assets/screenshot.png (README) |
| `assets/landscape/claude-squad/landing-*-fold.png` | our capture of the project site, 2026-10-31 |

The README embeds a demo video (GitHub user-attachment URL `.../assets/aef18253-e58f-4525-9032-f5a3d66c975a`), which I did not
download or watch. The screenshot shows four instances with green dots and `+/-` stats, a diff-highlighted Preview, and a key
legend at the bottom. No Jian screenshots exist.

## What I could not verify

Everything at runtime: how it behaves with many sessions, what the status dot distinguishes, the daemon's reliability, whether
the project is still actively maintained beyond the August release (50 open issues was the API count).

## Sources

[repo and README](https://github.com/smtg-ai/claude-squad) · [project site](https://smtg-ai.github.io/claude-squad/) ·
GitHub API metadata and `session/storage.go`, `daemon/daemon.go`, fetched 2026-11-12.
