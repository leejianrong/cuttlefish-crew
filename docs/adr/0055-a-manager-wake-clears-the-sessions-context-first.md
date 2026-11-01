# ADR-0055: A manager wake clears the session's context first

- Status: Accepted (built, CUT-273)
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
   server, hooks and the pre-approved tools all load again, so the instructions are intact.

## Decision

Option 4. `FleetDaemon.send_chat(..., fresh=True)` types `/clear`, Enter, waits 1.5 seconds, then the message. The manager's
two senders (`_send_wake`, `check_in_manager`) pass `fresh=True`. It is done only when the session's hooks say `idle`: a busy or
unknown session is never cleared (that would drop a turn in flight), and the existing refusals (no session, a dialog open, not
Claude Code) apply first. The reply says `context: fresh` when it cleared. Builders' chats never clear.

`CUTTLEFISH_MANAGER_KEEP_CONTEXT=1` turns it off, for measuring the old behaviour.

**Measurement.** `src/cuttlefish/sessions/usage.py` reads each turn's `message.usage` from a Claude Code transcript (the
session's own JSONL, counting a message once) and `python scripts/manager_tokens.py <project dir under ~/.claude/projects>`
prints requests and in/out/cache-create/cache-read tokens per prompt, file by file. With `/clear` each wake is its own file.

## Consequences

- A cleared wake costs the fixed prefix (system prompt, tools, `CLAUDE.md`) plus its own tool calls, and stays flat however many
  wakes came before. A person who chats with the manager between wakes loses that conversation at the next wake, by design;
  the journal and `recent_answers` hold what was done.
- The recorded session id goes stale after a `/clear`; a daemon restart then resumes the older conversation, or falls back to a
  fresh one, and the next wake clears it again.
- Not verified here against a live Claude Code (no logged-in scratch HOME was available to the agent): that `/clear` pasted
  into the TUI is taken as the command, and the tokens per wake before and after, remain to measure with the script.
