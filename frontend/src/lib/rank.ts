// Order of the project list (CUT-189): what needs a person first. Pure, but for the per-browser
// pins, which are a convenience that is safe to lose (like the theme).

import type { ProjectSummary } from "./api";

/** Lower sorts first: waiting for you, then stuck, then working, then everything quiet. */
export type Urgency = 0 | 1 | 2 | 3;

export function urgency(project: ProjectSummary, waiting: number): Urgency {
  if (waiting > 0) return 0;
  const roles = Object.values(project.status);
  if (roles.some((s) => s === "failed" || s === "blocked")) return 1;
  if (project.running) return 2;
  return 3;
}

/** The list in the order to show it: pinned first, then by urgency, then the most recently active,
 * then by name and id so equal projects never swap places between refreshes. */
export function rankProjects(
  projects: readonly ProjectSummary[],
  waitingBy: Readonly<Record<string, number>>,
  pinned: ReadonlySet<string>,
): ProjectSummary[] {
  const key = (p: ProjectSummary) => ({
    pin: pinned.has(p.id) ? 0 : 1,
    urgency: urgency(p, waitingBy[p.id] ?? 0),
    at: p.last_event_at ? Date.parse(p.last_event_at) || 0 : 0,
  });
  return [...projects].sort((a, b) => {
    const x = key(a);
    const y = key(b);
    return (
      x.pin - y.pin ||
      x.urgency - y.urgency ||
      y.at - x.at ||
      a.name.localeCompare(b.name) ||
      a.id.localeCompare(b.id)
    );
  });
}

const PINS = "cuttlefish.pinned-projects";

export function loadPins(): Set<string> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(PINS) ?? "[]");
    if (Array.isArray(parsed)) return new Set(parsed.filter((v): v is string => typeof v === "string"));
  } catch {
    // Blocked site data or a damaged value: nothing is pinned.
  }
  return new Set();
}

export function savePins(pins: ReadonlySet<string>): void {
  try {
    localStorage.setItem(PINS, JSON.stringify([...pins]));
  } catch {
    // The pin lasts until the page is closed.
  }
}
