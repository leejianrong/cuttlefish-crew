---
icon: lucide/calendar-clock
---

# Give the builder a goal on a schedule

A schedule asks a project's builder for a goal on a repeat, so something like "triage the open issues" is waiting for
you each morning. This page adds one, shows what happens when it fires, and lists what it will not do.

## Add a schedule

On the project page, **Scheduled goals** takes the goal text and a repeat: **Every N minutes**, **Every N hours**,
**Daily**, **Weekdays**, **Weekly on** a day, or **Cron**. From a terminal:

```bash
uv run cuttlefish schedule add <project-id> --goal "Triage the open issues" --recurrence "weekdays 06:30"
uv run cuttlefish schedule list
```

`list` prints one JSON object per line, soonest first, with `next_due`, `last_run` and `last_result`. `enable <id>`,
`disable <id>` and `remove <id>` do the rest. A running daemon notices a change at its next pass, so nothing needs a
restart.

**Times are UTC, always.** Not your browser's time zone and not the machine's: `daily 09:00` is 09:00 UTC all year, so
a daylight-saving change never moves or repeats a run. If you want 09:00 in London in summer, write 08:00.

| Recurrence | Meaning |
|---|---|
| `every <N>m`, `every <N>h` | N minutes or hours after the last run (at most 30 days) |
| `daily HH:MM` | Every day at that time |
| `weekdays HH:MM` | Monday to Friday |
| `weekly <day> HH:MM` | One day a week; `day` is `mon` to `sun` |
| `cron <m> <h> <dom> <mon> <dow>` | The usual five fields: `*`, a number, `a-b`, `a,b` and a `/step` (`*/15`, `10-50/10`). Sunday is 0 or 7. When both day fields are set, either may match, as in cron. No month or day names, no `@daily`. An expression that never matches (`0 0 31 2 *`) is refused when you add it. |

### Skip a run that is too late

`--grace MINUTES` (or **Skip if late by more than** on the page) makes a run that the daemon reaches more than that
many minutes after its slot get skipped instead of started: a "this morning" goal is not wanted at night after the
daemon was down. The skip is journaled and shown on the schedule (`missed by 95 minutes, past the 60 minute grace`),
and the next slot is the next recurrence, not a make-up run. Without a grace, a missed run still starts once on return.

### Only run when a command says so

`--precheck COMMAND` (or **Only run if this command succeeds**) runs a command before the goal starts and skips the
run unless it exits 0, for example `git diff --quiet origin/main`, or `test -f ready.flag`. The skip is journaled as
`skipped by precheck` with how it ended (`it exited 1`) and shown on the schedule. Because it is a command run by the
daemon, it gets the rules an agent's command gets:

- The **never-allowed list** holds in every mode, Auto included (`sudo`, a forced push, a download piped into a shell,
  writes outside the project).
- It must be on the **project's command list** (its presets and anything you allowed) or the project must be in Auto.
  A command that is not is skipped, never asked about, since nobody is there to answer. Ask first runs nothing.
- It runs through `/bin/sh -c` in the project folder with the same allowlisted environment as an agent: none of the
  daemon's secrets, no model key. Its output is discarded.
- It is killed after 30 seconds (`CUTTLEFISH_PRECHECK_TIMEOUT`), which also skips the run.
- A project placed on a [worker machine](cli-reference.md) is skipped with that reason: a precheck runs on this PC only.

The precheck is part of the schedule, set at the Admin tier like the rest of it. Any other failure to decide (it would
not start, the daemon is closing) also skips; a precheck that cannot answer never starts unattended work.

## What happens when one is due

`cuttlefish serve` looks every 20 seconds. A due schedule is first checked against its grace and its precheck, then
starts its goal the way **Give the builder a goal** does:
Autopilot is on, and your [**Its own branch**](branches-and-pull-requests.md) setting is respected. Autopilot plans
first, so an unattended run waits at the plan under **Needs you** until you approve it. It does not act with nobody
watching.

On the manager's project a due schedule is a check-in typed into its session instead of a goal; see
[waking the manager](manager-tools.md#waking-the-manager-on-a-schedule).

## What it will not do

- **Run in your time zone.** Times are UTC all year, so a daylight-saving change never moves or repeats a run.
  A cron expression is read in UTC too.
- **Run without the daemon.** Only `cuttlefish serve` runs schedules; a one-shot `cuttlefish run` never does. If the
  daemon was down when one was due, it runs once on return, not once per missed slot.
- **Interrupt a builder that is still on its last goal.** That run is skipped, journaled as a skipped schedule and shown on
  the page with the reason. The next try is one recurrence later. A builder whose last goal has ended (done, stopped,
  limit, stuck) is restarted with the new goal. One you started without Autopilot looks busy until you stop it.
- **Let anyone but an Admin change it**, since it starts work unattended.

A schedule makes no model call of its own. The agent you chose does the work, at your model credit.

## See also

- [`cuttlefish schedule`](cli-reference.md#cuttlefish-schedule) in the CLI reference.
- Why: [ADR-0051](https://github.com/leejianrong/cuttlefish-crew/blob/main/docs/adr/0051-a-schedule-asks-a-projects-builder-for-a-goal-from-the-fleet-daemon-and-never-interrupts-one.md).
