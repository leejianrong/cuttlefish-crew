// Unit: what the jump palette lists and how a key opens it (palette.ts).

import { describe, expect, it } from "vitest";
import type { NeedsYouRequest } from "./api";
import { isOpenKey, moveActive, paletteResults, type PaletteProject } from "./palette";

const projects: PaletteProject[] = [
  { id: "p1", name: "billing-api", tags: ["client:acme"] },
  { id: "p2", name: "docs-site" },
  { id: "p3", name: "mobile-billing" },
];
const asks = [{ id: "r1", project_id: "p2", project_name: "docs-site", title: "Run npm install" }] as NeedsYouRequest[];
const labels = (q: string, current = null as Parameters<typeof paletteResults>[3]) =>
  paletteResults(q, projects, asks, current).map((e) => `${e.group}:${e.label}`);

describe("paletteResults", () => {
  it("opens with what needs you, then the screens, then projects", () => {
    const r = labels("");
    expect(r[0]).toBe("Needs you:docs-site");
    expect(r).toContain("Go to:Role library");
    expect(r).toContain("Projects:billing-api");
  });
  it("lists the open project's tabs first when on a project", () => {
    const r = paletteResults("", projects, [], { view: "project", id: "p1", tab: "chat" });
    expect(r[0]).toMatchObject({ group: "This project", label: "Overview" });
    expect(r.filter((e) => e.group === "This project")).toHaveLength(8);
  });
  it("matches words anywhere, and prefers a start of the name", () => {
    const r = labels("billing");
    expect(r.slice(0, 2)).toEqual(["Projects:billing-api", "Projects:mobile-billing"]);
  });
  it("reaches a project's tab by name and tab", () => {
    const [first] = paletteResults("billing team", projects, [], null);
    expect(first.route).toEqual({ view: "project", id: "p1", tab: "team" });
  });
  it("finds a request by its text and says nothing when nothing matches", () => {
    expect(labels("npm")).toEqual(["Needs you:docs-site"]);
    expect(labels("zzzz")).toEqual([]);
  });
  it("caps the list, and a 100-project fleet answers well inside a frame", () => {
    const many = Array.from({ length: 100 }, (_, i) => ({ id: `s${i}`, name: `seed-${i}-web` }));
    const t0 = performance.now();
    const r = paletteResults("web", many, [], null);
    expect(performance.now() - t0).toBeLessThan(50);
    expect(r).toHaveLength(30);
  });
});

describe("keys", () => {
  const key = (over: Partial<KeyboardEvent>) =>
    ({ key: "k", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...over }) as KeyboardEvent;
  it("opens on Ctrl-K or Cmd-K only", () => {
    expect(isOpenKey(key({ ctrlKey: true }))).toBe(true);
    expect(isOpenKey(key({ metaKey: true, key: "K" }))).toBe(true);
    expect(isOpenKey(key({}))).toBe(false);
    expect(isOpenKey(key({ ctrlKey: true, shiftKey: true }))).toBe(false);
  });
  it("wraps the highlighted option at both ends", () => {
    expect(moveActive(2, 1, 3)).toBe(0);
    expect(moveActive(0, -1, 3)).toBe(2);
    expect(moveActive(0, 1, 0)).toBe(0);
  });
});
