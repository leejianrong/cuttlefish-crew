---
icon: lucide/git-pull-request
---

# Run a goal on its own branch and open a pull request

By default the builder edits your own checkout, on whatever branch you are on. This page shows how to give each goal
its own branch instead, and how to turn that branch into a GitHub pull request without leaving cuttlefish. It assumes
a registered project (see the [quickstart](quickstart.md)) whose folder is a git repository with at least one commit.

## Turn on a branch per goal

On the project page, switch on **Its own branch** in the goal box, then give the builder a goal. From a terminal, the
same switch is an Admin-tier call:

```bash
curl -X PATCH "$URL/api/projects/$PROJECT_ID/worktree-per-goal" \
  -H "x-cuttlefish-token: $TOKEN" -H "content-type: application/json" \
  -d '{"worktree_per_goal": true}'
```

It applies to the next goal, not to a builder already running. That goal gets a git worktree at
`<project>/.cuttlefish/worktrees/<slug>` on a new branch `cuttlefish/<slug>`, made from your checkout's current commit.
The slug is the goal's first words and the minute. The builder runs there, and the project page names the branch.

Check it from the project folder:

```bash
git worktree list
git log --oneline cuttlefish/<slug>
```

Your checkout and its branch are not touched. If the folder is not a repository, or has no commit, the goal is refused
with the reason; it does not quietly run in your checkout.

!!! note "Worktrees are kept"
    A finished goal's worktree stays, with its work on it. cuttlefish never merges it and never removes one on its own.
    See [Remove a worktree](#remove-a-worktree).

## Remove a worktree

When a goal is finished with, press **Remove worktree** under it on the project page, or run:

```bash
uv run cuttlefish projects remove-worktree <project-id>
uv run cuttlefish projects remove-worktree <project-id> --delete-branch   # also delete the branch, if it is merged
```

It removes the last goal's worktree folder. A worktree with uncommitted or untracked changes is refused, with the
reason, and so is one while the builder is still running (from the page or the CLI) or a folder git does not list as a
worktree; nothing is forced. The branch stays unless you ask for
`--delete-branch` (the page has a checkbox), and then only a merged branch goes: an unmerged one is kept and the
answer says so. The page's route is `POST /api/projects/{id}/goal/worktree/remove` (Admin tier).

## Open a pull request

When the goal is done, press **Open a pull request** under it on the project page, or run:

```bash
uv run cuttlefish projects pr <project-id>
uv run cuttlefish projects pr <project-id> --push   # push the branch to origin first
```

Either runs `gh pr create` in the goal's worktree against the default branch (`origin/HEAD`, else `main` or `master`)
and prints the URL. It uses the `gh` login already on this machine; cuttlefish stores no GitHub credential.

- **Title and body.** The title is the goal's first line. The body is the goal, what happened (from the journal) and the
  commit subjects ahead of the base, with known secrets removed. There is no model call.
- **It says why when it cannot.** `gh` missing or logged out, the branch is the default branch, there is no `origin`
  remote, no commits ahead, or the branch is not pushed yet. For the last one the page offers **Push and open it**;
  `--push` is the CLI equivalent. The push goes to `origin` only, for that one branch. The default branch and any force
  push are refused in every mode.
- **A branch that already has a pull request** returns that URL instead of making a second.
- **Admin tier, GitHub only, never merges.** The URL shows on the page and in the timeline.

The CLI's description has the goal and commits but not the page's digest lines, since those come from the daemon.

## A team's roles in a git project

A team with two or more kopicode-backed roles in a git project gives each role its own worktree and branch under
`<project>/.cuttlefish/worktrees/`, and merges them into one branch, `cuttlefish/team-<team id>`, at the end of every
round. **Your checkout is never touched:** the work lands on that branch, and you merge it or open a pull request from
it. A role that left files uncommitted has them committed on its branch first.

A merge that conflicts is aborted, not resolved. The timeline records the role and the files, and that role's branch
(`cuttlefish/team-<id>-<role>`) and worktree stay so its work is not lost. A project that is not a git repository with a
commit keeps today's behaviour. [ADR-0054](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0054-each-team-role-gets-its-own-worktree-merged-back-through-an-integration-branch.md).

## See also

- [Scheduled goals](schedules.md) start a goal on a repeat and respect this setting.
- [`cuttlefish projects`](cli-reference.md) in the CLI reference.
- Why: [ADR-0048](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0048-each-goal-can-run-in-its-own-git-worktree-and-branch-inside-the-project-root.md)
  and [ADR-0049](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0049-a-goal-branch-opens-a-github-pull-request-through-the-machines-own-gh-and-never-merges.md).
