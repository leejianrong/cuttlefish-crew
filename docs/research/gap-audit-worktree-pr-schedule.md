# Gap audit: worktrees, pull requests and scheduled runs (CUT-234)

Paseo has three things the landscape report (`fleet-manager-landscape.md`, PR #228) marks Unknown for
cuttlefish: a git worktree per agent, a pull-request flow across forges, and a `schedule`
command. This is what the code does today, read from `src/` on `origin/main` (2026-10-31). No
model was called; the claims below are from reading code and docs, not from a live run.

## (a) A separate git worktree per agent or per goal

**What exists.** Nothing creates one. A project has one `root`
(`src/cuttlefish/projects/store.py:44`, the `root TEXT NOT NULL` column) and every agent, the
tmux session included, runs in it (`sessions/manager.py:4`). Roles in a team share that root too
(`TeamInput.root` is singular, `team.py:11-14`).

The one cost this causes is known and accepted. Two kopicode roles on one root run one at a time,
because kopicode locks the working tree (`team.py:12-19`, Q44, `agent_docs/known-gaps.md`).
`team.py:17-19` and `docs/SLICES.md:685` name a worktree per role as "the actual fix",
deliberately deferred under ADR-0002.

There is one worktree-aware line: `fleet/gitexclude.py:24-26` skips a linked worktree because
its `.git` is a file. So a project whose root is a linked worktree would not get `.cuttlefish/`
and `.satay/` excluded from `git status`, and an agent's `git add .` could commit them.

**Missing.**
- Creating and removing a worktree per goal or per role (`git worktree add`), with a branch.
- A project root that can move to the worktree for a goal while the project's own checkout stays put.
- Exclude handling for linked worktrees (resolve `git rev-parse --git-path info/exclude`).
- A dashboard view of which worktree and branch a goal is on.

**Size.** 8 points as a whole, so split: the exclude fix (1), a worktree helper plus per-goal
checkout (5), the per-role fan-out that lifts the kopicode lock fallback (5, depends on the second).

## (b) Commit, push and open or merge a pull request from the product

**What exists.**
- Commit and branch: the `git-save` preset lets an agent run `git add`, `git commit`,
  `git switch -c` and `git checkout -b` (`delegate/presets.py:64-69`, description "Never pushes",
  `presets.py:146`). `git commit -m '<msg>'` has a quote-aware rule (`delegate/consent.py:49-58`).
- Read git: `git status/diff/log/show/blame/branch` are read-only presets (`presets.py:52-62`),
  and the Overview timeline runs `git log` for commits (`timeline.py:125-139`, 40 most recent on
  the checked-out branch, `known-gaps.md:310`).
- Guard rails: `git push --force` and its variants are never allowed
  (`delegate/never_allowed.py:24,250`).

**Missing.** Everything after the commit.
- No preset or route for `git push`; a push is a plain permission prompt, answered by a person.
- No forge integration: no `gh`, GitLab, Gitea or Forgejo call anywhere in `src/` (a grep for
  `gh pr`, `pull request` and `create_pr` finds only the consent and never-allowed lists).
- No "open a PR" or "merge" action, no PR status (checks, review) on the project page, no
  branch-per-goal naming convention.
- No remote or forge detection at project registration.

**Size.** 13 points as a whole, so split: a push preset with a branch-not-default guard (2);
open a PR through `gh` from a dashboard action, GitHub only (5); PR and checks status on the
project page (3); GitLab, Gitea and Forgejo adapters behind one seam (5, after the first).

## (c) Scheduled or recurring runs

**What exists.** No scheduler. `grep -ri "cron\|recurring\|schedul" src/` finds only
`fleet/autopilot.py`, whose `_schedule` (`autopilot.py:204`) is a debounce: it waits
`settle_s` after the builder's `Stop` hook and then decides whether to nudge. Autopilot has a
time limit (`autopilot.py:80`, `limits.hours`) but that bounds one run; it never starts one. The
CLI has no `schedule` command (`cli.py` subcommands: run, run-team, show, steer, approve,
secrets, init, doctor, projects, serve, relay, mcp, soak). The only recurring thing in the repo
is CI.

**Missing.**
- A stored schedule per project (cron expression or interval, a goal text, an on/off flag) in
  the project registry.
- A daemon loop that starts a goal when one is due, skips it when a run is still going, and
  journals that it did so.
- `cuttlefish schedule add/list/remove` and a dashboard form.
- A policy for an unattended run: Autopilot's limits apply, but a scheduled run with a prompt
  waiting has nobody to answer it; it should stop at Needs you and say so.

**Size.** 8 points, so split: the store and the due-check as pure logic with tests (3); the
daemon loop that starts the goal (3); CLI and dashboard surface (3).

## What we would file

One card per gap, under CUT-E29. The worktree exclude fix is the cheapest and a bug today.
The push-and-PR work is the biggest visible gap against Paseo. A schedule is the smallest
route to "runs while you sleep", which the portfolio vision plan leans on.

Cards filed under CUT-E29: worktree CUT-237, CUT-238, CUT-239; PR flow CUT-240 to CUT-243; schedule CUT-244 to CUT-246.
