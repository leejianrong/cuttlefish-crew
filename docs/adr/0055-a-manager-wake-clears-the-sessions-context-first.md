# ADR-0055: A manager wake can clear the session's context first (opt-in)

- Status: Accepted (built, CUT-273; opt-in after measurement)
- Date: 2026-11-01
- Deciders: development agent for CUT-273, open to review by Jian

## Context

The manager is one long-lived Claude Code session (ADR-0052). Every request it makes re-reads the whole conversation from the
prompt cache, so a context that only grows makes each wake dearer than the last. In the first real run two manager turns cost
about 460k cache-read tokens (CUT-269 had already removed the claude.ai connectors with `--strict-mcp-config`).

A wake (ADR-0053's approval wake, a scheduled check-in) carries no history the manager needs: it re-reads `list_attention` and
`project_digest`, which are the current truth, and the journal holds what it answered before. cuttlefish makes no model call
(ADR-0042), so the fix has to be a change to what is typed or how the session is launched.

## Options checked against `claude` 2.1.316

1. **Start a new session per wake** (a new `--session-id`, no `--resume`): works, but means killing and relaunching the tmux
   session, which loses the dashboard's terminal and chat thread each time and fights the resume-after-restart logic.
2. **`/compact`**: a model call to summarise, costing tokens to save tokens, and the summary still carries history.
3. **A compact wake message only**: already so (it names project ids, no digest). It does not touch the growing context.
4. **`/clear` typed into the idle session before the message.** Claude Code's own command: it starts a new conversation (a new
   session id and transcript file) in the same process, with no model call. `CLAUDE.md`, `--append-system-prompt`, the MCP
   server, hooks and the pre-approved tools load again.

## Decision

Option 4 is built, **off by default**: `CUTTLEFISH_MANAGER_CLEAR_CONTEXT=1` turns it on. (An earlier draft cleared by default
with a `CUTTLEFISH_MANAGER_KEEP_CONTEXT` opt-out; that name never shipped and is removed.)

`FleetDaemon.send_chat(..., fresh=True)` types `/clear`, Enter, waits 1.5 seconds, then the message. The manager's two senders
(`_send_wake`, `check_in_manager`) pass `fresh=manager_clear_context()`. It clears only when the session's hooks say `idle`: a
busy or unknown session is never cleared, and the existing refusals (no session, a dialog open, not Claude Code) apply first.
The reply says `context: fresh` when it cleared. Builders' chats never clear.

**Measurement** (real Claude Code 2.1.316 on Haiku, a small context, new in+out+cache-create tokens per wake). Cleared: 5
requests and about 9-10k tokens, because the system prompt, tools and `CLAUDE.md` are rebuilt in the cache each time and the
manager re-reads its digests. Kept: 3 requests and about 1-2k tokens, growing about 4k per wake. A clear therefore costs more
until the kept context outgrows about 8.5k of rebuilt prefix, which the run did not reach. That is why it is opt-in: it pays
for a manager that has run many wakes (or been chatted with at length), not for a fresh one. The cache-read figure that
prompted this ticket is the cost of a long context, and clearing bounds it; whether to pay the rebuild is the person's call.

Tooling to see it: `src/cuttlefish/sessions/usage.py` reads each turn's `message.usage` from a Claude Code transcript (a message
counted once) and `python scripts/manager_tokens.py <project dir under ~/.claude/projects>` prints requests and
in/out/cache-create/cache-read tokens per prompt, file by file. With `/clear` each wake is its own file.

## Consequences

- With it on, a wake's cost stays flat however many wakes came before. A person who chats with the manager between wakes loses
  that conversation at the next wake; the journal and `recent_answers` hold what was done.
- **The session id (CUT-274).** After a `/clear` the conversation has a new id. The daemon now records the newest `session_id`
  a hook reports (`sessions.manager.note_session_id`, Claude Code records only), so a restart resumes the latest conversation
  rather than the first one.
- **The first wake after a daemon restart is never cleared**: hook activity is in memory, so the state is `unknown` until the
  session has said something, and an unknown session is not cleared. The next wake clears it.
