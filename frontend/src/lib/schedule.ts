// The schedule form's rules (CUT-246): the recurrence text the daemon reads, and how a schedule is
// described on the page. Times are UTC, never the browser's zone, as the daemon reads them. Pure, so each is unit-tested.

export type RecurrenceKind = "every-m" | "every-h" | "daily" | "weekdays" | "weekly" | "cron";

export const KINDS: { id: RecurrenceKind; label: string }[] = [
  { id: "every-m", label: "Every N minutes" },
  { id: "every-h", label: "Every N hours" },
  { id: "daily", label: "Daily at (UTC)" },
  { id: "weekdays", label: "Weekdays at (UTC)" },
  { id: "weekly", label: "Weekly on (UTC)" },
  { id: "cron", label: "Cron (UTC)" },
];

export const DAYS: { id: string; label: string }[] = [
  { id: "mon", label: "Monday" },
  { id: "tue", label: "Tuesday" },
  { id: "wed", label: "Wednesday" },
  { id: "thu", label: "Thursday" },
  { id: "fri", label: "Friday" },
  { id: "sat", label: "Saturday" },
  { id: "sun", label: "Sunday" },
];

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

/** The text for the daemon, or null when the form is not yet a valid one. The daemon checks the
 * values of a cron field again; this only catches a wrong shape. */
export function buildRecurrence(
  kind: RecurrenceKind,
  every: number,
  time: string,
  extra: { day: string; cron: string } = { day: "mon", cron: "" },
): string | null {
  if (kind === "daily" || kind === "weekdays") return TIME.test(time) ? `${kind} ${time}` : null;
  if (kind === "weekly") {
    return TIME.test(time) && DAYS.some((d) => d.id === extra.day) ? `weekly ${extra.day} ${time}` : null;
  }
  if (kind === "cron") {
    const fields = extra.cron.trim().split(/\s+/);
    return fields.length === 5 && fields.every((f) => /^[0-9*,/-]+$/.test(f)) ? `cron ${fields.join(" ")}` : null;
  }
  if (!Number.isInteger(every) || every < 1) return null;
  if (every > (kind === "every-m" ? 43200 : 720)) return null; // 30 days, as the daemon allows
  return `every ${every}${kind === "every-m" ? "m" : "h"}`;
}

/** The grace field: blank is no grace; else a whole number of minutes up to a week. */
export function graceMinutes(text: string): { ok: true; value: number | null } | { ok: false } {
  const t = text.trim();
  if (t === "") return { ok: true, value: null };
  const n = Number(t);
  return /^\d+$/.test(t) && n >= 1 && n <= 10080 ? { ok: true, value: n } : { ok: false };
}

export function graceLine(s: { grace_minutes?: number | null }): string | null {
  return s.grace_minutes ? `Skipped if it is reached more than ${s.grace_minutes} minutes late.` : null;
}

export function precheckLine(s: { precheck?: string | null }): string | null {
  return s.precheck ? `Runs only if ${s.precheck} exits 0.` : null;
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
  if (daily) return `Daily at ${daily[1]} UTC`;
  const weekdays = /^weekdays (\d\d:\d\d)$/.exec(recurrence);
  if (weekdays) return `Weekdays at ${weekdays[1]} UTC`;
  const weekly = /^weekly (\w{3}) (\d\d:\d\d)$/.exec(recurrence);
  const day = DAYS.find((d) => d.id === weekly?.[1]);
  if (weekly && day) return `Every ${day.label} at ${weekly[2]} UTC`;
  return recurrence.startsWith("cron ") ? `Cron ${recurrence.slice(5)}, UTC` : recurrence;
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
  if (s.last_result === "started") return `Started the goal at ${at}.`;
  const byPrecheck = "skipped by precheck: ";
  if (s.last_result.startsWith(byPrecheck)) {
    return `Skipped by the precheck at ${at}: ${s.last_result.slice(byPrecheck.length)}.`;
  }
  return `Not started at ${at}: ${s.last_result.replace(/^skipped: /, "")}.`;
}
