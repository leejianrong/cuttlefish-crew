# ADR-0051: A schedule asks a project's builder for a goal from the fleet daemon, and never interrupts one

- Status: Accepted (built, CUT-244, CUT-245, CUT-246; amended by CUT-297, below)
- Date: 2026-10-31
- Deciders: Jian (cards CUT-244 to CUT-246), development agent

## Context

Nothing in cuttlefish started work on its own (`docs/research/gap-audit-worktree-pr-schedule.md`): Autopilot's
`_schedule` only debounces after a `Stop` hook. "Runs while you sleep" needs a stored schedule, a loop that acts on
it, and rules for a builder that is busy.

## Decision

1. **A schedule is a project, a goal text, a recurrence, an enabled flag, `last_run`, `last_result` and `next_due`**,
   in a `schedules` table of `projects.db` (`cuttlefish.projects.schedules`). `due(now)` is pure.
2. **The recurrence is one of `every <N>m`, `every <N>h` or `daily HH:MM`, read in UTC.** A cron subset would be more
   to parse, document and get wrong for no use yet. UTC means a daylight-saving change cannot move or repeat a run, and
   a person sees the same clock on the page and in the CLI.
3. **`next_due` moves on from the time a run was handled, not from the slot.** A schedule due while the daemon was down
   runs once on restart. Enabling a paused schedule counts from that moment.
4. **The fleet daemon owns the loop** (`fleet/scheduler.py`, one asyncio task, 20 s). It is started by `cuttlefish
   serve` after agent sessions are reconciled and cancelled in `FleetDaemon.close()`. The one-shot CLI never runs
   schedules (AGENTS.md: only the daemon acts on its own).
5. **A due schedule starts its goal through `FleetDaemon.give_goal`, with Autopilot on**, so worktree-per-goal
   (ADR-0048), limits and the journal all behave as for a person. Autopilot gives the goal an end, and its plan-first
   step is where an unattended run stops at Needs you instead of acting without anyone. It makes no model call.
6. **An unfinished goal is never interrupted.** A running session whose Autopilot has not ended, or which has no
   Autopilot, is busy: the run is skipped and `ScheduleSkipped` (with the reason) is journaled and shown. A running
   session whose Autopilot ended (done, stopped, limit, stuck) is stopped and given the new goal. A skipped run is not
   retried early: the next try is one recurrence on.
7. **The surface is Admin tier**: `GET`/`POST /api/projects/{id}/schedules`, `PATCH`/`DELETE /api/schedules/{id}`, the
   `cuttlefish schedule` commands, and a card on the project page. A schedule starts work unattended.

## Consequences

- A goal given by hand without Autopilot looks busy forever; the person stops it to let a schedule through.
- A person who is reading a finished builder's chat when a schedule fires loses that session. The finished goal's
  work is on its branch when worktree-per-goal is on.
- A schedule whose project was removed records a skip and keeps being tried each recurrence until removed.

## Amendment: presets, cron, a missed-run grace and a precheck (CUT-297, 2026-11-12)

Decision 2 said a cron subset would be "more to parse, document and get wrong for no use yet". Orca's automations
(cron, a missed-run grace window, a precheck) showed the use, so the recurrence grows, and stays UTC:

- `weekdays HH:MM`, `weekly <mon..sun> HH:MM` and `cron <5 fields>` join `every` and `daily`. Cron is five numeric
  fields with `*`, lists, ranges and steps, read in UTC; no names, no `@` forms, no seconds. One matcher serves them
  all (`next_after`). An expression with no day in five years is refused at add time.
- **A schedule may carry a grace** (minutes, at most a week). A run the daemon reaches later than that past its slot is
  skipped and journaled; `next_due` still moves on from the time it was handled (decision 3), so a missed slot is never
  made up. No grace keeps decision 3's "once on return".
- **A schedule may carry a precheck**, a command that must exit 0. It is the one place the daemon runs a command a
  person typed, so it is held to the agent's rules, not trusted: the project's `ConsentPolicy` (one never-allowed list,
  never duplicated; Auto allows what is not never-allowed; Ask first allows nothing) decides it, and a command no rule
  approves is skipped rather than asked about. It runs `/bin/sh -c` in the project root with `merge_env`'s allowlisted
  environment, no stdin, output discarded, in its own process group killed at `CUTTLEFISH_PRECHECK_TIMEOUT` (30 s). Only
  a clean exit 0 lets the run go on; anything else, including a failure to start, skips with a "skipped by precheck"
  reason. A project on a worker machine is skipped (the root is not on this PC). It is journaled as `ScheduleSkipped`,
  whose reason carries no command text, so nothing a person typed reaches the journal; the command lives only in the
  schedules table. It is never a task argument: the loop is an asyncio task in the daemon, not a satay task.
- The routes, CLI and card gain `grace_minutes` and `precheck`; the tier stays Admin.

