# ADR-0048: Each goal can run in its own git worktree and branch, inside the project root

- Status: Accepted (built, CUT-238)
- Date: 2026-10-31
- Deciders: Jian (card CUT-238), development agent

## Context

A project has one root and every agent runs in it (`docs/research/gap-audit-worktree-pr-schedule.md`). A goal given to
the builder therefore edits the person's own checkout, on whatever branch it is on. The never-allowed list and the
Claude Code edit hook both mean "inside the project root", so a worktree placed beside the project would be refused
as a write outside it.

## Decision

1. **A project setting, `worktree_per_goal`, off by default.** A project that does not turn it on behaves exactly as
   before. `PATCH .../worktree-per-goal` (Admin tier) and a switch on the goal card set it; it applies to the next goal.
2. **The worktree is inside the root:** `<root>/.cuttlefish/worktrees/<slug>`, on a new branch `cuttlefish/<slug>` made
   from the checkout's current `HEAD`. The slug is the goal's first words and the minute. Because it is under the
   root, `inside(root, path)` and every relative-path rule hold unchanged, and `.cuttlefish/` is already excluded
   from `git status` (the daemon adds it to `info/exclude` first). The checkout and its branch are never touched.
3. **Only the session's working directory moves.** The session record, hooks file, goal record and Autopilot files
   stay in the project root; `SessionManager.ensure(cwd=...)` changes where the tool is launched. A relaunch (daemon
   restart) goes back to the last goal's worktree while it exists.
4. **The goal record names it** (`branch`, `worktree`) and the goal card shows the branch.
5. **Failure is loud.** A folder that is not a repository, or has no commit, refuses the goal with the reason; it
   does not quietly run in the checkout. A session that fails to start removes the worktree it just made.
6. **A finished goal's worktree is kept**, with the work on it. Stale state is cleared when the next one is made:
   `git worktree prune`, and a leftover unregistered folder is removed. Nothing deletes a worktree that has work.

## Consequences

- Two goals' work is on two branches the person can review and merge; nothing is pushed or merged by cuttlefish.
- The kopicode `--root` lock is unchanged: a kopicode round and a goal still share the project's root lock. The
  interactive session cannot take a goal for kopicode, so this affects Claude Code and Codex goals only. Lifting the
  lock per role is the separate fan-out card.
- Worktrees accumulate until the person removes them: `cuttlefish projects remove-worktree` and the page's Remove
  worktree button (CUT-250) refuse uncommitted changes and keep the branch unless asked. Never automatic.
- A builder already running is not moved: the setting applies when a goal starts a session.
