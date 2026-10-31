---
icon: lucide/calendar-clock
---

# Give the builder a goal on a schedule

A schedule asks a project's builder for a goal on a repeat, so something like "triage the open issues" is waiting for
you each morning. This page adds one, shows what happens when it fires, and lists what it will not do.

## Add a schedule

On the project page, **Scheduled goals** takes the goal text and one of **Every N minutes**, **Every N hours** or
**Daily at (UTC)**. From a terminal:

```bash
uv run cuttlefish schedule add <project-id> --goal "Triage the open issues" --recurrence "daily 06:30"
uv run cuttlefish schedule list
```

`list` prints one JSON object per line, soonest first, with `next_due`, `last_run` and `last_result`. The recurrence is
`every <N>m`, `every <N>h` or `daily HH:MM`. `enable <id>`, `disable <id>` and `remove <id>` do the rest. A running
daemon notices a change at its next pass, so nothing needs a restart.

## What happens when one is due

`cuttlefish serve` looks every 20 seconds. A due schedule starts its goal the way **Give the builder a goal** does:
Autopilot is on, and your [**Its own branch**](branches-and-pull-requests.md) setting is respected. Autopilot plans
first, so an unattended run waits at the plan under **Needs you** until you approve it. It does not act with nobody
watching.

## What it will not do

- **Run in your time zone.** Times are UTC all year, so a daylight-saving change never moves or repeats a run.
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
