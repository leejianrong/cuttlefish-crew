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

## Auto always uses a branch

A project in **Auto** runs unattended, so nothing it does may land on the default branch unreviewed. Its goals get their
own branch whether or not **Its own branch** is on (a folder that is not a repository refuses the goal, as above). In a
Claude Code session, a command that would commit, merge, rebase, pull, reset or cherry-pick while the default branch is
checked out, or that switches to it, is refused before it runs, and the refusal shows in the project's history as denied
by `system`. A push to the default branch was already on the never-allowed list. This reads the command as text, so
`git -C <other folder>` and a script are not seen; Codex and kopicode sessions are held only by the branch they start on.

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

## See what the branch changed

The **Changes** card on the project page lists every file the goal's branch has changed against the project's default
branch, with its added and removed line counts; press a file to open its hunks. It reads from git each time you open
the page or press **Refresh**, and keeps no copy. It shows the branch's commits and, while the worktree exists, the
work not yet committed, including new files git does not track. Once the worktree is removed it shows the branch's
commits alone.

- **Secrets are not shown.** A file named like a secret (`.env`, `*.pem`, `*.key`, SSH keys, anything under
  `.cuttlefish/`) or one your `.gitignore` names appears with its name and counts and no lines. Files git ignores never
  appear, and neither do cuttlefish's own `.cuttlefish/` and `.satay/` folders. Nothing else is redacted: it is your own code, on your own dashboard.
- **Big diffs are cut, and it says so.** A file shows its first 1,000 changed lines; past about 1.5 MB of patch, or 300
  files, the rest are listed or counted without lines. Binary files show as binary.
- **A goal with no branch** (it ran in your own checkout) has nothing to compare, and the card says to turn on **Its
  own branch**. A project placed on a worker machine says its code is on that machine and the diff is not shown
  here yet.

**Send a note on a line.** Press the line number of an added or removed line to write a note about it, and send it to
the builder. The message names the file, the side and the line, quotes the line itself, and says which branch and commit
(and whether there were uncommitted changes) the line was seen in, so the agent can still find it if the numbers have
moved. If the branch has changed since the diff was read, the page says so and asks before sending. Files whose lines
are not shown (withheld, too large, binary) have no line numbers to press.

Where the note goes depends on the project. A project with a running team sends it to the role you pick, as a
[steer](quickstart.md) (Approve tier). A builder session gets it as chat, which only a running Claude Code builder
takes (Terminal tier, the same as typing in its terminal); for any other builder the box says so and offers **Copy**, so
you can paste it in the Terminal tab. A note is never sent while the builder is waiting on a permission, because the
dialog would read it as the answer.

The route is `GET /api/projects/{id}/goal/diff` (View tier: it only reads, and the journal at the same tier already
shows what the agent wrote). A refusal is a `200` with `ok: false`, a `code` and a plain `reason`.

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

A merge that conflicts is aborted, not resolved. The project's **Settings, Team** tab shows the team's branch and a card per role, with the conflicted role and its files marked, and the activity log records the same, and that role's branch
(`cuttlefish/team-<id>-<role>`) and worktree stay so its work is not lost. A project that is not a git repository with a
commit keeps today's behaviour, with the roles run one at a time. In a git project they run at the same time. [ADR-0054](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0054-each-team-role-gets-its-own-worktree-merged-back-through-an-integration-branch.md).

## See also

- [Scheduled goals](schedules.md) start a goal on a repeat and respect this setting.
- [`cuttlefish projects`](cli-reference.md) in the CLI reference.
- Why: [ADR-0048](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0048-each-goal-can-run-in-its-own-git-worktree-and-branch-inside-the-project-root.md)
  and [ADR-0049](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0049-a-goal-branch-opens-a-github-pull-request-through-the-machines-own-gh-and-never-merges.md).
