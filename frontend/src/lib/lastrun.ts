// What a finished run says about itself, for the Overview once nothing is running. Derived from
// the journal and the per-role status the daemon already sends; no second source.

import type { EpisodicEventView, RoleStatus, RoleUsage } from "./api";

export interface LastRun {
  /** `failed` wins over `stopped`, which wins over `finished`. */
  outcome: "finished" | "failed" | "stopped";
  /** Files any role changed, each counted once. */
  files: number;
  /** Whole minutes from the first event to the last, at least 1. */
  minutes: number;
  tokens: number;
  /** null when no role reported a cost (Codex reports none). */
  cost_usd: number | null;
  /** Why each failed role failed, from the journal. */
  failures: { role: string; error: string }[];
  /** What each role said when its last delegation completed (its `DelegationCompleted.summary`),
   * in the order roles first finished. Empty for a role that never completed. */
  answers: { role: string; text: string }[];
  /** Roles that were never given a task in this run, in the order the daemon lists them. */
  notRun: string[];
}

/** The run that just ended, or null while nothing has run yet or a role is still working or
 * waiting (a run that is not running but not over either is not described as over). */
export function lastRun(
  events: readonly EpisodicEventView[],
  status: Record<string, RoleStatus>,
  usage: Record<string, RoleUsage>,
): LastRun | null {
  // A role that was never given a task has no events and reads "queued" for good: the daemon
  // starts only the roles named in a start. Once nothing is running it will not run, so it
  // neither holds the run open nor counts toward how it ended.
  const notRun = Object.entries(status)
    .filter(([, s]) => s === "queued")
    .map(([role]) => role);
  const states = Object.values(status).filter((s) => s !== "queued");
  if (events.length === 0 || states.length === 0) return null;
  if (!states.every((s) => s === "done" || s === "failed" || s === "stopped")) return null;

  const paths = new Set<string>();
  const errors = new Map<string, string>();
  const said = new Map<string, string>();
  for (const event of events) {
    if (event.event_type === "DelegationCompleted") {
      const edited = event.payload.edited_paths;
      if (Array.isArray(edited)) for (const path of edited) paths.add(String(path));
      const { role, summary } = event.payload;
      if (typeof role === "string" && typeof summary === "string" && summary.trim() !== "") {
        said.set(role, summary.trim());
      }
    }
    if (event.event_type === "TaskFailed") {
      const role = event.payload.role;
      if (typeof role === "string") errors.set(role, String(event.payload.error ?? ""));
    }
  }

  const failures = Object.entries(status)
    .filter(([, s]) => s === "failed")
    .map(([role]) => ({ role, error: errors.get(role) ?? "" }));
  const outcome = states.includes("failed")
    ? "failed"
    : states.includes("stopped")
      ? "stopped"
      : "finished";

  const first = Date.parse(events[0].ts);
  const last = Date.parse(events[events.length - 1].ts);
  const minutes = Number.isFinite(first) && Number.isFinite(last)
    ? Math.max(1, Math.round((last - first) / 60000))
    : 1;

  const totals = Object.values(usage);
  const costs = totals.map((u) => u.cost_usd).filter((c): c is number => c !== null);
  return {
    outcome,
    files: paths.size,
    minutes,
    tokens: totals.reduce((sum, u) => sum + u.tokens, 0),
    cost_usd: costs.length > 0 ? costs.reduce((sum, c) => sum + c, 0) : null,
    failures,
    answers: [...said].map(([role, text]) => ({ role, text })),
    notRun,
  };
}

/** `builder was not started`, `builder and tester were not started`, or null when every role ran. */
export function notRunText(run: LastRun): string | null {
  const names = run.notRun;
  if (names.length === 0) return null;
  const list =
    names.length === 1
      ? names[0]
      : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `${list} ${names.length === 1 ? "was" : "were"} not started: a start runs only the roles given a task.`;
}

/** `41,200` tokens as `41k`, small counts as they are. */
function tokenText(tokens: number): string {
  return tokens >= 1000 ? `${Math.round(tokens / 1000)}k tokens` : `${tokens} tokens`;
}

export function runHeading(run: LastRun): string {
  return run.outcome === "finished"
    ? "The last run finished"
    : run.outcome === "failed"
      ? "The last run failed"
      : "The last run was stopped";
}

/** `3 files changed · 12 min · 41k tokens · $0.04`, leaving out a cost nobody reported. */
export function runFigures(run: LastRun): string {
  const parts = [
    run.files === 1 ? "1 file changed" : `${run.files} files changed`,
    `${run.minutes} min`,
    tokenText(run.tokens),
  ];
  if (run.cost_usd !== null) parts.push(`$${run.cost_usd.toFixed(2)}`);
  return parts.join(" · ");
}
