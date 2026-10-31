# ADR-0049: A goal's branch opens a GitHub pull request through the machine's own `gh`, and never merges

- Status: Accepted (built, CUT-241)
- Date: 2026-10-31
- Deciders: Jian (card CUT-241), development agent

## Context

A goal on its own branch (ADR-0048) ends as local commits. Getting them reviewed meant leaving the product.
`gap-audit-worktree-pr-schedule.md` found no push preset and no forge call anywhere. CUT-240 added the push
(a named non-default branch; the default branch and every force form are never allowed). ADR-0046's tiers say
a person decides what leaves the machine.

## Decision

1. **One action, `POST /api/projects/{id}/pull-request`** (body `{"push": bool}`), and `cuttlefish projects pr <id>
   [--push]`. Both call `fleet/pullrequest.open_pull_request`, which runs `gh pr create` in the goal's worktree (else
   the project root) with `--base` the default branch (`origin/HEAD`, else main or master) and `--head` the goal's
   branch (else the checked-out one).
2. **The machine's own login, nothing of ours.** `gh` runs with an allowlisted environment (PATH, HOME and
   the `GH_*` token variables the person already has). cuttlefish stores no forge credential and adds none.
3. **No model call** (ADR-0042). The title is the goal's first line. The body is the goal text, the page's digest
   lines (derived from the journal, ADR-0004) and the commit subjects ahead of the base. Every piece goes through
   the journal's `Redactor` first; nothing else of the journal goes in.
4. **Refusals are answers, not errors**: `200 {ok: false, code, reason}` with a plain reason for `gh` missing, not
   logged in, no branch, the default branch, no origin remote, no commits ahead, and `not_pushed`. For `not_pushed`
   the page offers "Push and open it" (`push: true`). The push is checked against the never-allowed list
   (`never_allowed_reason`) before it runs, repository hooks are switched off for it, and it goes to `origin` only.
5. **Journaled and read back.** `PullRequestOpened` (url, branch, base, title) is appended to the project's session
   journal; the goal endpoint returns `pr_url` from the latest one since the goal was given, and the timeline shows it.
   A branch that already has a pull request returns that URL, flagged `existing`.
6. **Admin tier.** It leaves the machine, so it sits with the other settings and not with approve.
7. **Never merges, never force-pushes, GitHub only.** Another forge, PR status and checks are later cards.

## Consequences

- The CLI body has no digest (it needs the daemon's overview); the goal and commits are there.
- A pull request needs a remote named `origin`. Other remote names are not read.
- `gh` is trusted as the person's own tool: it can read its own config and token as that user, like any tool they run.
