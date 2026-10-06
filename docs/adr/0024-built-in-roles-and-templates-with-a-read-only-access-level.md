# ADR-0024: Built-in roles and team templates, and a read-only access level

Status: accepted (docs/SLICES.md V4-B)

## Context

Registering a project meant writing a prompt for every role and inventing a team shape.
The placeholder roles (`builder: ships fast`) were one-line personas, and a role with no
persona got no prompt at all. Operator feedback (2026-10-06): defaults should exist.

## Decision

- `cuttlefish.roles` defines five built-in roles (builder, reviewer, tester, planner,
  docs-writer) with real prompts, and three templates: solo-builder, builder-reviewer
  (the default) and full-crew. A built-in is registered as an ordinary `RoleDefinition`
  whose persona is its prompt. There is no link back: a role is "default" exactly while its
  persona equals the built-in prompt, and "reset" copies the prompt back.
- Registration with no roles and no template (CLI, `POST /api/projects`) gets the default
  template. An explicit empty `roles` list stays empty. `--role reviewer` with no persona
  is the built-in reviewer.
- `RoleDefinition.access` is `None` (the project's access) or `"read-only"`, stored inside
  `roles_json` (no migration) and in `PersistedRole` so a resume rebuilds the same team.
  It rides `RoleInput` to `delegate_to_agent_backend` as an `access` argument **only when
  set**, so every other role's recorded arguments, and an in-flight run's replay, are
  unchanged.
- Read-only means the shell is `presets.READ_ONLY_PRESETS` (inspect, git-read) and declared
  commands are ignored. The never-allowed list applies as always (ADR-0023).
- Reviewer and planner are read-only; builder, tester and docs-writer are not. Docs-writer
  is prompt-only ("change documentation files only"); no path restriction exists.
- No suggested backend per role: a hint nothing applies would be noise. Backend choice
  stays role, then project, then environment (ADR-0022).

## Consequences

- Read-only is **not** edit-proof. kopicode treats in-root edits as implicit consent,
  Claude Code runs under `acceptEdits`, and Codex's sandbox follows the allow list. The
  permission-modes slice (V4-C) maps access to each backend's real controls; until then the
  prompt is the guard, and known-gaps.md says so. The dashboard must not claim otherwise.
- Prompts open with the instruction because `_compose_role_text` prepends "You are
  {name}." Changing that sentence would change every stored persona's composed text.
- A project registered before this keeps its roles; nothing is rewritten.
