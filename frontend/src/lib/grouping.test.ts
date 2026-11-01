// Unit: filtering and grouping the project list (grouping.ts).

import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProjectSummary, RoleStatus } from "./api";
import {
  filterProjects,
  groupProjects,
  limitGroups,
  loadCollapsed,
  NO_FILTER,
  parseTags,
  saveCollapsed,
  stateCounts,
  tagCounts,
  UNTAGGED,
} from "./grouping";

const NOW = Date.parse("2026-11-02T12:00:00Z");

function project(id: string, tags: string[], status: RoleStatus = "done"): ProjectSummary {
  return {
    id,
    name: id,
    tags,
    status: { builder: status },
    running: false,
    require_approval: false,
    last_event_at: new Date(NOW - 60_000).toISOString(),
  } as unknown as ProjectSummary;
}

const list = [
  project("a", ["client:acme", "rust"], "working"),
  project("b", ["client:acme"], "failed"),
  project("c", ["rust"], "done"),
  project("d", [], "done"),
];
const ids = (ps: ProjectSummary[]) => ps.map((p) => p.id);
afterEach(() => vi.unstubAllGlobals());

describe("filterProjects", () => {
  it("shows everything with no filter", () => {
    expect(ids(filterProjects(list, NO_FILTER, {}, NOW))).toEqual(["a", "b", "c", "d"]);
  });

  it("keeps a project that carries every selected tag", () => {
    const filter = { states: new Set<never>(), tags: new Set(["client:acme", "rust"]) };
    expect(ids(filterProjects(list, filter, {}, NOW))).toEqual(["a"]);
  });

  it("filters by state, any of them", () => {
    const filter = { states: new Set(["stuck" as const, "working" as const]), tags: new Set<string>() };
    expect(ids(filterProjects(list, filter, {}, NOW))).toEqual(["a", "b"]);
  });

  it("counts a waiting request as the waiting state", () => {
    const filter = { states: new Set(["waiting" as const]), tags: new Set<string>() };
    expect(ids(filterProjects(list, filter, { c: 1 }, NOW))).toEqual(["c"]);
  });
});

describe("counts", () => {
  it("lists tags most used first", () => {
    expect(tagCounts(list)).toEqual([
      { tag: "client:acme", count: 2 },
      { tag: "rust", count: 2 },
    ]);
  });

  it("counts states", () => {
    expect(stateCounts(list, {}, NOW)).toEqual({ working: 1, waiting: 0, done: 2, stuck: 1, idle: 0 });
  });
});

describe("groupProjects", () => {
  it("groups by first tag, untagged last in ranked order, and keeps order inside a group", () => {
    const groups = groupProjects(list, "tag", {}, NOW);
    expect(groups.map((g) => [g.label, ids(g.projects)])).toEqual([
      ["client:acme", ["a", "b"]],
      ["rust", ["c"]],
      [UNTAGGED, ["d"]],
    ]);
  });

  it("groups by state", () => {
    expect(groupProjects(list, "state", {}, NOW).map((g) => g.label)).toEqual([
      "Working",
      "Stuck",
      "Done",
    ]);
  });

  it("is one group with no grouping", () => {
    expect(groupProjects(list, "none", {}, NOW)).toHaveLength(1);
  });
});

describe("limitGroups", () => {
  it("draws the first n projects across groups and keeps each heading's full count", () => {
    const groups = groupProjects(list, "tag", {}, NOW);
    const drawing = limitGroups(groups, 3);
    expect(drawing.map((g) => [g.label, g.projects.length, ids(g.shown)])).toEqual([
      ["client:acme", 2, ["a", "b"]],
      ["rust", 1, ["c"]],
    ]);
    expect(limitGroups(groups, 100).flatMap((g) => ids(g.shown))).toEqual(["a", "b", "c", "d"]);
  });
});

describe("memory", () => {
  it("round-trips collapsed groups and survives blocked storage", () => {
    const store: Record<string, string> = {};
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => void (store[k] = v),
    });
    saveCollapsed(new Set(["rust"]));
    expect([...loadCollapsed()]).toEqual(["rust"]);
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("blocked");
      },
    });
    expect(loadCollapsed().size).toBe(0);
  });
});

describe("parseTags", () => {
  it("splits on commas and drops blanks", () => {
    expect(parseTags(" client:acme, rust ,, ")).toEqual(["client:acme", "rust"]);
  });
});
