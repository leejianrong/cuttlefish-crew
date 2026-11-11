// The jump palette (CUT-294): Ctrl-K / Cmd-K, or the search button, jumps to a screen, a project, a
// project's tab or a project that needs you. Pure: it filters what the app already polls (the
// project list and the waiting requests), so typing makes no request.

import type { NeedsYouRequest } from "./api";
import { PROJECT_TABS, type ProjectTab, type Route } from "./route";

export interface PaletteEntry {
  id: string;
  label: string;
  /** Dimmer text after the label. */
  hint: string;
  group: "Needs you" | "Go to" | "This project" | "Projects" | "Project tabs";
  route: Route;
  /** What is matched against; lowercase. */
  haystack: string;
}

export interface PaletteProject {
  id: string;
  name: string;
  tags?: string[];
}

export const TAB_LABEL: Record<ProjectTab, string> = {
  overview: "Overview",
  chat: "Chat",
  terminal: "Terminal",
  artifacts: "Artifacts",
  "needs-you": "Needs you",
  permissions: "Permissions",
  team: "Team",
  secrets: "Secrets",
};

const SCREENS: { label: string; route: Route; words: string }[] = [
  { label: "Projects", route: { view: "projects" }, words: "home portfolio list" },
  { label: "Needs you", route: { view: "needs-you" }, words: "inbox requests approvals" },
  { label: "Role library", route: { view: "roles" }, words: "roles team" },
  { label: "Shared secrets", route: { view: "secrets" }, words: "keys credentials" },
  { label: "Devices", route: { view: "devices" }, words: "pair phone relay" },
  { label: "Add project", route: { view: "add" }, words: "new register folder" },
];

const ORDER: PaletteEntry["group"][] = ["Needs you", "This project", "Go to", "Projects", "Project tabs"];

/** Waiting requests, one entry per project, with its first request as the hint. */
function needsYou(projects: readonly PaletteProject[], requests: readonly NeedsYouRequest[]): PaletteEntry[] {
  const names = new Map(projects.map((p) => [p.id, p.name]));
  const seen = new Set<string>();
  const out: PaletteEntry[] = [];
  for (const r of requests) {
    if (seen.has(r.project_id)) continue;
    seen.add(r.project_id);
    const name = names.get(r.project_id) ?? r.project_name;
    out.push({
      id: `need:${r.project_id}`,
      label: name,
      hint: r.title,
      group: "Needs you",
      route: { view: "project", id: r.project_id, tab: "needs-you" },
      haystack: `${name} ${r.title} needs you waiting`.toLowerCase(),
    });
  }
  return out;
}

const screens = (): PaletteEntry[] =>
  SCREENS.map((s) => ({
    id: `go:${s.label}`,
    label: s.label,
    hint: "",
    group: "Go to",
    route: s.route,
    haystack: `${s.label} ${s.words}`.toLowerCase(),
  }));

const tabsOf = (p: PaletteProject, group: PaletteEntry["group"]): PaletteEntry[] =>
  PROJECT_TABS.map((tab) => ({
    id: `tab:${p.id}:${tab}`,
    label: TAB_LABEL[tab],
    hint: group === "This project" ? "" : p.name,
    group,
    route: { view: "project", id: p.id, tab },
    haystack: `${p.name} ${TAB_LABEL[tab]}`.toLowerCase(),
  }));

const projectEntry = (p: PaletteProject): PaletteEntry => ({
  id: `project:${p.id}`,
  label: p.name,
  hint: (p.tags ?? []).join(" "),
  group: "Projects",
  route: { view: "project", id: p.id, tab: "overview" },
  haystack: `${p.name} ${(p.tags ?? []).join(" ")}`.toLowerCase(),
});

/** Higher is better; -1 when a word of the query is nowhere in the text. A match at the start of
 * the label, or of a word, beats one in the middle. */
export function score(query: string, entry: PaletteEntry): number {
  let total = 0;
  for (const word of query.toLowerCase().split(/\s+/).filter(Boolean)) {
    const at = entry.haystack.indexOf(word);
    if (at < 0) return -1;
    total += at === 0 ? 3 : /[\s\-_:/.]/.test(entry.haystack[at - 1] ?? "") ? 2 : 1;
  }
  return total;
}

/** What the palette lists for `query`, best first, at most `limit`. An empty query is a short
 * menu: what needs you, this project's tabs, the screens and the first few projects. */
export function paletteResults(
  query: string,
  projects: readonly PaletteProject[],
  requests: readonly NeedsYouRequest[],
  current: Route | null,
  limit = 30,
): PaletteEntry[] {
  const needs = needsYou(projects, requests);
  const here =
    current?.view === "project" ? projects.filter((p) => p.id === current.id).flatMap((p) => tabsOf(p, "This project")) : [];
  if (query.trim() === "") {
    return [...needs.slice(0, 5), ...here, ...screens(), ...projects.slice(0, 8).map(projectEntry)].slice(0, limit);
  }
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const all = [...needs, ...here, ...screens(), ...projects.map(projectEntry), ...projects.flatMap((p) => tabsOf(p, "Project tabs"))];
  return all
    .map((entry, index) => ({ entry, index, s: score(query, entry) }))
    .filter((m) => m.s >= 0)
    // A project's tab is offered only when the query names the tab, or it would crowd out the projects.
    .filter((m) => m.entry.group !== "Project tabs" || words.some((w) => m.entry.label.toLowerCase().includes(w)))
    .sort(
      (a, b) =>
        b.s - a.s || ORDER.indexOf(a.entry.group) - ORDER.indexOf(b.entry.group) || a.index - b.index,
    )
    .slice(0, limit)
    .map((m) => m.entry);
}

/** The keystroke that opens the palette: Ctrl-K, or Cmd-K on a Mac. */
export function isOpenKey(event: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey" | "shiftKey">): boolean {
  return event.key.toLowerCase() === "k" && (event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey;
}

/** The option to highlight after an arrow key, wrapping at both ends. */
export function moveActive(active: number, delta: 1 | -1, count: number): number {
  if (count === 0) return 0;
  return (active + delta + count) % count;
}
