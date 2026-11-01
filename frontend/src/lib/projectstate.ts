// One word and one sentence of "what is going on" for a project (CUT-188), derived from what the
// project summary already carries (the journal's role statuses and last event, plus the requests
// waiting for a person). No new field and no second source of truth.

import type { ProjectSummary } from "./api";
import { agoIso } from "./artifacts";

export type ProjectState = "working" | "waiting" | "done" | "stuck" | "idle";

export interface StateLine {
  state: ProjectState;
  reason: string;
}

/** No journal event for this long, on a project that should be moving, is stuck. A round's own
 * timeout is 2 hours (`DEFAULT_ROUND_TIMEOUT_S`), so a quieter project than that has lost its round. */
export const STUCK_AFTER_MS = 3 * 60 * 60 * 1000;

export const STATE_LABEL: Record<ProjectState, string> = {
  working: "Working",
  waiting: "Waiting",
  done: "Done",
  stuck: "Stuck",
  idle: "Idle",
};

/** The rule, in order (docs-site/quickstart.md has the same list):
 * 1. waiting: a request needs a person, or a round ended and the review gate wants a person.
 * 2. idle: no role has started, or the operator stopped the team.
 * 3. stuck: a role failed, or a role that should be moving has no journal event for 3 hours.
 * 4. working: a role is mid-round (or between rounds) or a team is running.
 * 5. done: every role that started has finished. */
export function projectState(
  project: ProjectSummary,
  waiting: number,
  now = Date.now(),
): StateLine {
  const roles = Object.entries(project.status);
  const named = (status: string) => roles.filter(([, s]) => s === status).map(([n]) => n);
  const last = project.last_event_at ? Date.parse(project.last_event_at) : Number.NaN;
  const ago = agoIso(project.last_event_at, now / 1000);

  if (waiting > 0) {
    return { state: "waiting", reason: `${waiting} ${waiting === 1 ? "request needs" : "requests need"} you` };
  }
  const blocked = named("blocked");
  if (project.require_approval && blocked.length > 0) {
    return { state: "waiting", reason: `${blocked[0]} is waiting for your review` };
  }
  const started = roles.filter(([, s]) => s !== "queued");
  if (started.length === 0 && !project.running) return { state: "idle", reason: "Not started yet" };
  const stopped = named("stopped");
  if (stopped.length > 0 && !project.running) {
    return { state: "idle", reason: `Stopped${ago ? ` ${ago}` : ""}` };
  }
  const failed = named("failed");
  if (failed.length > 0) {
    return { state: "stuck", reason: `${failed[0]} failed${ago ? ` ${ago}` : ""}` };
  }
  const moving = project.running || started.some(([, s]) => s === "working" || s === "blocked");
  if (moving) {
    if (!Number.isNaN(last) && now - last >= STUCK_AFTER_MS) {
      return { state: "stuck", reason: `No progress for ${ago.replace(/ ago$/, "")}` };
    }
    const who = named("working")[0] ?? blocked[0];
    return { state: "working", reason: who ? `${who} is working` : "A team is running" };
  }
  return { state: "done", reason: `Finished${ago ? ` ${ago}` : ""}` };
}
