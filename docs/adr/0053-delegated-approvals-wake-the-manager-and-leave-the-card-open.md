# ADR-0053: Delegated approvals wake the manager and leave the card open

- Status: Accepted (built, CUT-230)
- Date: 2026-11-01
- Deciders: development agent for CUT-230, open to review by Jian

## Context

ADR-0046 puts a manager agent at tier 2, and ADR-0050 and ADR-0052 gave it a credential, tools and a session. Nothing told
it a request had been raised: it acted only when a person messaged it.

## Decision

1. **`CUTTLEFISH_DELEGATE_APPROVALS=1` (off by default, daemon-wide).** When a permission request is raised in any project
   but the manager's own, the daemon types one message into the manager's session (the chat path, `send_chat`) naming the
   projects with approvals waiting. Bursts are coalesced into one message and spaced 30 seconds apart. The manager then reads
   `list_attention` and `project_digest` and answers with `answer_attention`.
2. **The card stays in Needs you.** Nothing is hidden from the person. A manager that is down, busy at a dialog, or unsure
   leaves the request open for the person until the request window ends, or raises a decision, which only a person answers.
3. **No new rules.** Never-allowed refusal, the allow-once/deny limit, self-exclusion and the `manager` journal entry with its
   tier are ADR-0050's, enforced in the broker and daemon. Delegation adds a wake and nothing to trust.

## Consequences

- Needs a running manager session on Claude Code (the chat is typed in for Claude Code only); otherwise the wake is logged
  and skipped.
- The setting is global, not per project; a per-project switch can come with a use for it.
