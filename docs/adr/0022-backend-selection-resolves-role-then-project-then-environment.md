# ADR-0022: Backend selection resolves role, then project, then environment

Status: accepted (KAN-1809)

## Context

`CUTTLEFISH_AGENT_BACKEND` was one choice per process, so every role in every
project under one `cuttlefish serve` shared a backend. That blocked the "crew"
pitch (Claude Code reviewing what kopicode wrote) and mixed-project fleets.

## Decision

- `RoleDefinition.backend` (persisted inside `roles_json`, no migration) and
  `Project.backend` (a nullable column, migrated like `max_tokens`) are optional.
  Resolution is role, then project, then `CUTTLEFISH_AGENT_BACKEND`.
- The project backend is the runtime default for that project's team
  (`prepare_run(agent_backend=...)`); a role's backend rides on `RoleInput` and
  is passed to `delegate_to_agent_backend` as an optional `agent_backend`
  argument, **only when set**, so a role naming none has byte-identical task
  arguments and an in-flight run still replays.
- `PersistedRole` carries the backend so `resume_pending` rebuilds the identical
  `TeamInput`.
- Every named backend's CLI is `PATH`-checked in `prepare_run` before the team
  starts (Q17), not discovered mid-task.
- `_needs_sequential_dispatch` counts only roles actually on kopicode: two
  kopicode roles still collide on its working-tree lock (Q44); roles on
  different backends run concurrently.
- Surfaces: `run-team --role-backend NAME=B`, `projects add --backend B
  --role-backend NAME=B`, the daemon HTTP API (`backend` on projects and roles,
  validated to a 400), and the dashboard register form and role labels. The MCP
  server's `register_project` is unchanged (ADR-0020's deliberately small surface).

## Consequences

Per-role credentials are still ambient per backend (ADR-0018's Codex auth gap
and Q37 apply per role). `cuttlefish run` (single task) has no per-task backend
flag; the environment variable still selects it.
