// Unit: the project list's order (rank.ts).

import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProjectSummary, RoleStatus } from "./api";
import { loadPins, rankProjects, savePins, urgency } from "./rank";

function project(
  id: string,
  over: { status?: RoleStatus; running?: boolean; at?: string | null } = {},
): ProjectSummary {
  return {
    id,
    name: id,
    status: over.status ? { builder: over.status } : {},
    running: over.running ?? false,
    last_event_at: over.at ?? null,
  } as unknown as ProjectSummary;
}

const ids = (list: ProjectSummary[]) => list.map((p) => p.id);
afterEach(() => vi.unstubAllGlobals());

describe("rankProjects", () => {
  it("puts waiting, then stuck, then working, then quiet", () => {
    const list = [
      project("quiet", { status: "done" }),
      project("working", { status: "working", running: true }),
      project("failed", { status: "failed" }),
      project("waiting", { running: true }),
    ];
    expect(ids(rankProjects(list, { waiting: 2 }, new Set()))).toEqual([
      "waiting",
      "failed",
      "working",
      "quiet",
    ]);
  });

  it("orders equals by recency, then name, and does not depend on input order", () => {
    const a = project("a", { at: "2026-11-01T10:00:00Z" });
    const b = project("b", { at: "2026-11-01T12:00:00Z" });
    const c = project("c");
    const d = project("d");
    expect(ids(rankProjects([d, c, a, b], {}, new Set()))).toEqual(["b", "a", "c", "d"]);
    expect(ids(rankProjects([a, b, c, d], {}, new Set()))).toEqual(["b", "a", "c", "d"]);
  });

  it("keeps a pinned project on top, even over a waiting one", () => {
    const list = [project("w", { running: true }), project("p", { status: "done" })];
    expect(ids(rankProjects(list, { w: 1 }, new Set(["p"])))).toEqual(["p", "w"]);
  });

  it("does not change the input", () => {
    const list = [project("b"), project("a")];
    rankProjects(list, {}, new Set());
    expect(ids(list)).toEqual(["b", "a"]);
  });
});

describe("urgency", () => {
  it("counts a failed role as stuck", () => {
    expect(urgency(project("x", { status: "failed" }), 0)).toBe(1);
  });
});

describe("pins", () => {
  it("round-trips, and survives blocked storage and a damaged value", () => {
    const data: Record<string, string> = {};
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => data[k] ?? null,
      setItem: (k: string, v: string) => {
        data[k] = v;
      },
    });
    savePins(new Set(["a", "b"]));
    expect([...loadPins()]).toEqual(["a", "b"]);
    vi.stubGlobal("localStorage", { getItem: () => "{oops", setItem: () => {} });
    expect(loadPins().size).toBe(0);
    const blocked = () => {
      throw new Error("blocked");
    };
    vi.stubGlobal("localStorage", { getItem: blocked, setItem: blocked });
    expect(loadPins().size).toBe(0);
    expect(() => savePins(new Set(["a"]))).not.toThrow();
  });
});
