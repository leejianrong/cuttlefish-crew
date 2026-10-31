// The schedule form's rules (CUT-246): the recurrence text the daemon reads, and how a schedule is
// described on the page. Times are UTC, as the daemon reads them. Pure, so each is unit-tested.

export type RecurrenceKind = "every-m" | "every-h" | "daily";

export const KINDS: { id: RecurrenceKind; label: string }[] = [
  { id: "every-m", label: "Every N minutes" },
  { id: "every-h", label: "Every N hours" },
  { id: "daily", label: "Daily at (UTC)" },
];

/** The text for the daemon, or null when the form is not yet a valid one. */
export function buildRecurrence(kind: RecurrenceKind, every: number, time: string): string | null {
  if (kind === "daily") return /^([01]\d|2[0-3]):[0-5]\d$/.test(time) ? `daily ${time}` : null;
  if (!Number.isInteger(every) || every < 1) return null;
  return `every ${every}${kind === "every-m" ? "m" : "h"}`;
}

export function canAdd(goal: string, recurrence: string | null): boolean {
  return goal.trim().length > 0 && recurrence !== null;
}

/** "Every 2 hours", "Daily at 06:30 UTC". */
export function describeRecurrence(recurrence: string): string {
  const every = /^every (\d+)([mh])$/.exec(recurrence);
  if (every) {
    const n = Number(every[1]);
    const unit = every[2] === "m" ? "minute" : "hour";
    return n === 1 ? `Every ${unit}` : `Every ${n} ${unit}s`;
  }
  const daily = /^daily (\d\d:\d\d)$/.exec(recurrence);
  return daily ? `Daily at ${daily[1]} UTC` : recurrence;
}

/** "07:15 UTC, in 12 minutes"; a paused schedule has no next run. */
export function nextLine(s: { enabled: boolean; next_due: string }, now: Date): string {
  if (!s.enabled) return "Paused";
  const at = new Date(s.next_due);
  const clock = `${at.toISOString().slice(11, 16)} UTC`;
  const minutes = Math.ceil((at.getTime() - now.getTime()) / 60_000);
  if (minutes <= 0) return `${clock}, due now`;
  if (minutes < 60) return `${clock}, in ${minutes} minute${minutes === 1 ? "" : "s"}`;
  if (minutes >= 48 * 60) return `${at.toISOString().slice(0, 10)} ${clock}`;
  const hours = Math.round(minutes / 60);
  return `${clock}, in ${hours} hour${hours === 1 ? "" : "s"}`;
}

/** What happened the last time it was due, or null before it first runs. */
export function resultLine(s: {
  last_run: string | null;
  last_result: string | null;
}): string | null {
  if (!s.last_run || !s.last_result) return null;
  const at = `${s.last_run.slice(0, 10)} ${s.last_run.slice(11, 16)} UTC`;
  return s.last_result === "started"
    ? `Started the goal at ${at}.`
    : `Not started at ${at}: ${s.last_result.replace(/^skipped: /, "")}.`;
}
