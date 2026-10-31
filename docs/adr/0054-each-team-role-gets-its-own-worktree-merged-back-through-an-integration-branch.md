# ADR-0054: Each team role gets its own worktree, merged back through an integration branch

- Status: Proposed (first slice built, CUT-239; the wiring into `team.py` is not)
- Date: 2026-11-01
- Deciders: development agent for CUT-239, for Jian to confirm before the wiring

## Context

Two kopicode-backed roles in one team share the project's `--root`, and kopicode refuses a second session on one working
tree, so `team._dispatch_round` runs them one at a time (Q44, Q54). The fix named then is a worktree per role (ADR-0048
built the worktree machinery for goals).

Wiring it is more than a dispatch change, which is why this card stops at a design and its git half:

- **Where the work lands changes.** Today every role edits the person's checkout. Per-role worktrees must merge
  somewhere, and merging into the person's checkout would break ADR-0048's promise to leave it alone. So a team's work
  lands on a branch the person merges, the same as a goal's.
- **`root` is keyed everywhere.** The delegation's `root` also keys kopicode's conversation registry, handover reads, the
  never-allowed "inside the root" check and the DelegationStarted journal event. A role's worktree is inside the project
  root, so the path check holds, but the conversation key and the journal must still name the project root, not the
  worktree.
- **Replay.** Making worktrees is a side effect, so it has to be a satay task whose result is journaled, not a call in the
  workflow body, or a resumed run would try to make them again.
- **Conflicts** have no UI (out of scope for the card), so they must be reported, not resolved.

## Decision

1. **An integration branch per team**, `cuttlefish/team-<id>`, in its own worktree under
   `<root>/.cuttlefish/worktrees/`, made from `HEAD`. Each role works on `cuttlefish/team-<id>-<role>` in a worktree made
   from the integration branch. (A `/` between team and role would collide with the integration ref.)
2. **Merge back after each role finishes, in role order**, with `merge --no-ff`. Work a role left uncommitted is committed
   on its branch first, so nothing is lost. A conflicting merge is aborted and reported with the files; that role's branch
   and worktree stay and the other roles still merge. cuttlefish never resolves a conflict.
3. **Only with a git repository.** A project root that is not a repository with a commit keeps today's sequential fallback,
   so the fallback is gone for git projects and kept for the rest. Nothing changes for a team that does not have two
   kopicode roles.
4. **The checkout is never touched.** The person merges the integration branch (or opens a pull request from it, ADR-0049).

## Consequences

- Built now: `cuttlefish.sessions.role_trees` (`prepare`, `merge_back`, `remove_merged`) with tests on a real repository.
  Nothing calls it yet.
- Next slices, in order: (a) a journaled satay task that calls `prepare` and gives `_dispatch_round` a per-role delegation
  directory while `root` stays the project's for keys and journal; (b) `merge_back` at the end of a round and a journal
  event naming conflicts; (c) drop the sequential branch for git roots and update the known gap; (d) show the team's
  branch and any conflicted role on the team tab.
- A team's work no longer appears in the checkout for a git project with two kopicode roles. That is a visible change and
  is why this is Proposed.
