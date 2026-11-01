// Filtering and grouping the project list (CUT-191). Pure but for the per-browser memory of how
// the list is grouped and which groups are collapsed, a convenience that is safe to lose.

import type { ProjectSummary } from "./api";
import { projectState, STATE_LABEL, type ProjectState } from "./projectstate";

export type GroupBy = "none" | "tag" | "state";

export interface Filter {
  /** Show only these states; empty shows all. */
  states: ReadonlySet<ProjectState>;
  /** Show only projects that carry every one of these tags. */
  tags: ReadonlySet<string>;
}

export const NO_FILTER: Filter = { states: new Set(), tags: new Set() };

export interface Group {
  key: string;
  label: string;
  projects: ProjectSummary[];
}

const tagsOf = (p: ProjectSummary): readonly string[] => p.tags ?? [];

export function isFiltering(filter: Filter): boolean {
  return filter.states.size > 0 || filter.tags.size > 0;
}

export function filterProjects(
  projects: readonly ProjectSummary[],
  filter: Filter,
  waitingBy: Readonly<Record<string, number>>,
  now = Date.now(),
): ProjectSummary[] {
  return projects.filter(
    (p) =>
      (filter.states.size === 0 ||
        filter.states.has(projectState(p, waitingBy[p.id] ?? 0, now).state)) &&
      [...filter.tags].every((t) => tagsOf(p).includes(t)),
  );
}

/** Every tag in use, with how many projects carry it, most used first. */
export function tagCounts(projects: readonly ProjectSummary[]): { tag: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const p of projects) for (const t of tagsOf(p)) counts.set(t, (counts.get(t) ?? 0) + 1);
  return [...counts]
    .map(([tag, count]) => ({ tag, count }))
    .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
}

export function stateCounts(
  projects: readonly ProjectSummary[],
  waitingBy: Readonly<Record<string, number>>,
  now = Date.now(),
): Record<ProjectState, number> {
  const counts: Record<ProjectState, number> = { working: 0, waiting: 0, done: 0, stuck: 0, idle: 0 };
  for (const p of projects) counts[projectState(p, waitingBy[p.id] ?? 0, now).state]++;
  return counts;
}

/** The first `limit` projects across `groups`, in order, each group keeping its full `projects`
 * (its heading counts them all) and gaining `shown`: how many of them to draw yet. A group with
 * none to draw yet is dropped. Drawing a fleet in chunks lets the first screen appear at once. */
export function limitGroups(groups: readonly Group[], limit: number): (Group & { shown: ProjectSummary[] })[] {
  let left = limit;
  const out: (Group & { shown: ProjectSummary[] })[] = [];
  for (const group of groups) {
    const shown = group.projects.slice(0, left);
    left -= shown.length;
    if (shown.length > 0) out.push({ ...group, shown });
  }
  return out;
}

export const UNTAGGED = "Untagged";

/** Groups in the order of `projects` (already ranked): a group sits where its first project does.
 * A project is grouped under its first tag; one with no tag goes under "Untagged". */
export function groupProjects(
  projects: readonly ProjectSummary[],
  by: GroupBy,
  waitingBy: Readonly<Record<string, number>>,
  now = Date.now(),
): Group[] {
  const groups = new Map<string, Group>();
  for (const p of projects) {
    const key =
      by === "tag"
        ? (tagsOf(p)[0] ?? UNTAGGED)
        : by === "state"
          ? STATE_LABEL[projectState(p, waitingBy[p.id] ?? 0, now).state]
          : "";
    const group = groups.get(key) ?? { key, label: key, projects: [] };
    group.projects.push(p);
    groups.set(key, group);
  }
  return [...groups.values()];
}

const GROUP_KEY = "cuttlefish.project-group-by";
const COLLAPSED_KEY = "cuttlefish.project-groups-collapsed";

export function loadGroupBy(): GroupBy {
  try {
    const value = localStorage.getItem(GROUP_KEY);
    if (value === "tag" || value === "state") return value;
  } catch {
    // Blocked site data: the list is not grouped.
  }
  return "none";
}

export function saveGroupBy(by: GroupBy): void {
  try {
    localStorage.setItem(GROUP_KEY, by);
  } catch {
    // Lasts until the page is closed.
  }
}

export function loadCollapsed(): Set<string> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? "[]");
    if (Array.isArray(parsed)) return new Set(parsed.filter((v): v is string => typeof v === "string"));
  } catch {
    // Damaged or blocked: every group is open.
  }
  return new Set();
}

export function saveCollapsed(collapsed: ReadonlySet<string>): void {
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...collapsed]));
  } catch {
    // Lasts until the page is closed.
  }
}

/** The text of a tags field: comma-separated, as typed. */
export function parseTags(text: string): string[] {
  return text.split(",").map((t) => t.trim()).filter(Boolean);
}
