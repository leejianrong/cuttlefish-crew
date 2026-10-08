import { describe, expect, it } from "vitest";
import { clearDraft, hasDrafts, loadDraft, resetDrafts, saveDraft } from "./drafts";
import { keepIfSame, sameJson } from "./same";

describe("sameJson and keepIfSame", () => {
  it("compares by data, not identity", () => {
    expect(sameJson({ a: [1, { b: 2 }] }, { a: [1, { b: 2 }] })).toBe(true);
    expect(sameJson({ a: 1 }, { a: 2 })).toBe(false);
    expect(sameJson(null, null)).toBe(true);
  });
  it("keeps the held object when nothing changed", () => {
    const held = { running: true, status: { a: "working" } };
    expect(keepIfSame(held, { running: true, status: { a: "working" } })).toBe(held);
    const changed = { running: true, status: { a: "done" } };
    expect(keepIfSame(held, changed)).toBe(changed);
  });
});

describe("drafts", () => {
  it("keeps an unsaved edit per project and part until cleared", () => {
    resetDrafts();
    saveDraft("p1", "permissions", { mode: "auto" });
    saveDraft("p1", "role:builder", { persona: "x" });
    expect(loadDraft("p1", "permissions")).toEqual({ mode: "auto" });
    expect(loadDraft("p2", "permissions")).toBeUndefined();
    expect(hasDrafts("p1")).toBe(true);
    expect(hasDrafts("p2")).toBe(false);
    clearDraft("p1", "permissions");
    expect(hasDrafts("p1")).toBe(true); // the role draft is still there
    clearDraft("p1", "role:builder");
    expect(hasDrafts("p1")).toBe(false);
  });
});
