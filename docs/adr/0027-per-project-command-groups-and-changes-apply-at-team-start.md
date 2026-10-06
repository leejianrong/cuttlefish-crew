# ADR-0027: Command groups are chosen per project, and a settings change applies when the team starts

Status: accepted (docs/SLICES.md V4-F)

## Context

The default presets (ADR-0023) were all-or-nothing: a project could add commands but not switch
a group off or opt into Go/Rust or containers. The Permissions tab needs switches. Separately,
checking the V4-C claim that a mode change "applies from the next round" showed it was wrong.

## Decision

- `Project.presets` is the list of command groups switched on, in a nullable column (`NULL` is
  the defaults, so existing rows are unchanged). `allow` stays what the operator adds on top.
  `PATCH /api/projects/{id}/presets` validates names against the catalogue (400 on an unknown
  one) and stores them in catalogue order. An empty list is no groups, not the defaults.
- `resolve_allow(declared, presets)` uses the chosen groups instead of `DEFAULT_PRESETS`. The
  delegation task takes an optional `presets` argument, **passed only when the project's set
  differs from the defaults**, and `PersistedRole` carries it, for the same reason `access` does:
  recorded arguments and replay are unchanged for every project that never touches it.
- Read-only still gets only the inspection groups, whatever is switched on (ADR-0024).
- `GET /api/permissions` returns the modes, the presets with their commands, the never-allowed
  list and the per-backend notes, all defined beside the code that enforces them
  (`permissions.py`, `presets.py`, `never_allowed.py`), so the screen cannot say something the
  daemon does not do.
- **A change to the mode, the groups, your own commands or the roles applies the next time the
  team starts.** `FleetDaemon.start` composes every role's input once; a running team keeps
  what it started with (and what resume rebuilds). The dashboard says so when a team is
  running. The V4-C text that said "next round" was wrong and is corrected.
- Roles are edited per project (the Team tab). The Roles destination is a read-only library of
  built-ins: each project keeps its own copy, so a "global" edit would change nothing.

## Consequences

- To change permissions on a live team the operator stops and restarts it. A mid-run change
  would need the round loop to re-read the project, which is a separate decision.
- Command groups are per project, not per role; "Tester: Standard + containers" from the
  mockup is not expressible yet.
