// The Overview's mission-control parts (CUT-176): the digest's "since", the timeline's times and
// dots, and the progress ring. Pure but for the per-browser memory of the last visit, which is a
// convenience that is safe to lose (like the theme).

import type { AutopilotStatus } from "./autopilot";

export type TimelineKind =
  | "needs_you"
  | "autopilot"
  | "refresh"
  | "image"
  | "tests"
  | "commit"
  | "note";

export interface TimelineEvent {
  at: string;
  kind: TimelineKind;
  text: string;
  /** An artifact path for an image, a request id for a Needs you, a commit id for a commit. */
  ref: string | null;
}

export interface OverviewData {
  since: string;
  branch: string | null;
  goal: { text: string; at?: string; backend?: string } | null;
  autopilot: AutopilotStatus;
  digest: string[];
  timeline: TimelineEvent[];
}

const KEY = "cuttlefish.lastvisit.";
/** A visit this recent is not "away": the digest would only repeat what is on screen. */
export const AWAY_AFTER_MS = 10 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

export function loadLastVisit(projectId: string, now = Date.now()): string {
  try {
    const raw = localStorage.getItem(KEY + projectId);
    if (raw && !Number.isNaN(Date.parse(raw))) return raw;
  } catch {
    // Blocked site data: fall through to a day back.
  }
  return new Date(now - DAY_MS).toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function saveLastVisit(projectId: string, now = Date.now()): void {
  try {
    localStorage.setItem(KEY + projectId, new Date(now).toISOString().replace(/\.\d{3}Z$/, "Z"));
  } catch {
    // The next visit just looks back a day.
  }
}

/** Whether to show "While you were away": there is something to say, and it has been a while. */
export function awayWorthShowing(since: string, digest: readonly string[], now = Date.now()): boolean {
  return digest.length > 0 && now - Date.parse(since) >= AWAY_AFTER_MS;
}

/** `Since 18:00 yesterday`, `Since 09:30`, or `Since 21 Oct, 08:15`, in the viewer's own time. */
export function sinceText(since: string, now = new Date()): string {
  const then = new Date(since);
  if (Number.isNaN(then.getTime())) return "";
  const clock = then.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(now) - day(then)) / 86_400_000);
  if (days === 0) return `Since ${clock}`;
  if (days === 1) return `Since ${clock} yesterday`;
  return `Since ${then.toLocaleDateString([], { day: "numeric", month: "short" })}, ${clock}`;
}

/** The time column of a timeline row: the clock today, with the day added for an older one. */
export function eventWhen(at: string, now = new Date()): string {
  const when = new Date(at);
  if (Number.isNaN(when.getTime())) return "";
  const clock = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const same = when.toDateString() === now.toDateString();
  return same ? clock : `${when.toLocaleDateString([], { day: "numeric", month: "short" })} ${clock}`;
}

/** Which of the design's dot colours a kind gets: coral only for something waiting on a person. */
export function dotTone(kind: TimelineKind): "attention" | "accent" | "plain" {
  if (kind === "needs_you") return "attention";
  if (kind === "autopilot" || kind === "refresh") return "accent";
  return "plain";
}

/** The ring's stroke offset for `progress` (0 to 1) on a circle of `radius`. */
export function ringOffset(progress: number, radius: number): number {
  const clamped = Math.min(1, Math.max(0, progress));
  return 2 * Math.PI * radius * (1 - clamped);
}

/** One line under the goal: what the builder is on now, from the plan. */
export function nowLine(autopilot: AutopilotStatus): string {
  const plan = autopilot.plan;
  if (autopilot.phase === "awaiting_approval") return "The plan is waiting for you to approve it.";
  if (autopilot.phase === "planning") return "The builder is writing its plan.";
  const next = plan?.steps.find((step) => !step.done);
  if (autopilot.phase === "done") return "Every step of the plan is done.";
  if (autopilot.phase === "stuck" || autopilot.phase === "limit") return autopilot.reason ?? "";
  if (autopilot.phase === "paused") return next ? `Paused. Next: ${next.text}.` : "Paused.";
  if (next) return `Now: ${next.text}. It will only stop for you if it is blocked.`;
  return plan && plan.total > 0 ? "No steps left." : "";
}
