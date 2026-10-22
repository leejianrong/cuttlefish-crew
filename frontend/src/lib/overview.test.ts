// Unit: the Overview's mission-control parts (overview.ts).

import { afterEach, describe, expect, it, vi } from "vitest";
import type { AutopilotStatus } from "./autopilot";
import {
  AWAY_AFTER_MS,
  awayWorthShowing,
  dotTone,
  eventWhen,
  loadLastVisit,
  nowLine,
  ringOffset,
  saveLastVisit,
  sinceText,
} from "./overview";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("last visit", () => {
  it("defaults to a day back and round-trips what was saved", () => {
    const data: Record<string, string> = {};
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => data[k] ?? null,
      setItem: (k: string, v: string) => {
        data[k] = v;
      },
    });
    const now = Date.parse("2026-10-22T12:00:00Z");
    expect(loadLastVisit("p1", now)).toBe("2026-10-21T12:00:00Z");
    saveLastVisit("p1", now);
    expect(loadLastVisit("p1", now + 1000)).toBe("2026-10-22T12:00:00Z");
    expect(loadLastVisit("other", now)).toBe("2026-10-21T12:00:00Z");
  });

  it("survives blocked storage and a damaged value", () => {
    const blocked = () => {
      throw new Error("blocked");
    };
    vi.stubGlobal("localStorage", { getItem: blocked, setItem: blocked });
    expect(loadLastVisit("p", Date.parse("2026-10-22T12:00:00Z"))).toBe("2026-10-21T12:00:00Z");
    expect(() => saveLastVisit("p")).not.toThrow();
    vi.stubGlobal("localStorage", { getItem: () => "garbage", setItem: () => {} });
    expect(loadLastVisit("p", Date.parse("2026-10-22T12:00:00Z"))).toBe("2026-10-21T12:00:00Z");
  });
});

describe("awayWorthShowing", () => {
  const now = Date.parse("2026-10-22T12:00:00Z");
  it("needs something to say and a visit that was a while ago", () => {
    expect(awayWorthShowing("2026-10-22T08:00:00Z", ["1 commit."], now)).toBe(true);
    expect(awayWorthShowing("2026-10-22T08:00:00Z", [], now)).toBe(false);
    expect(awayWorthShowing(new Date(now - AWAY_AFTER_MS + 1000).toISOString(), ["x"], now)).toBe(false);
  });
});

describe("times in words", () => {
  const now = new Date(2026, 9, 22, 12, 0, 0);
  it("says the clock today, yesterday and the date before that", () => {
    const today = new Date(2026, 9, 22, 9, 30).toISOString();
    const yesterday = new Date(2026, 9, 21, 18, 0).toISOString();
    const older = new Date(2026, 9, 18, 8, 15).toISOString();
    expect(sinceText(today, now)).toMatch(/^Since \d/);
    expect(sinceText(yesterday, now)).toMatch(/yesterday$/);
    expect(sinceText(older, now)).toMatch(/^Since .*18|Oct/);
    expect(sinceText("nonsense", now)).toBe("");
  });
  it("keeps the day out of today's rows", () => {
    expect(eventWhen(new Date(2026, 9, 22, 9, 30).toISOString(), now)).toMatch(/^\d{1,2}[:.]\d{2}/);
    expect(eventWhen(new Date(2026, 9, 21, 9, 30).toISOString(), now).length).toBeGreaterThan(6);
    expect(eventWhen("nope", now)).toBe("");
  });
});

describe("dotTone", () => {
  it("is coral only for a person being needed", () => {
    expect(dotTone("needs_you")).toBe("attention");
    expect(dotTone("refresh")).toBe("accent");
    expect(dotTone("commit")).toBe("plain");
  });
});

describe("ringOffset", () => {
  it("is the full circle at none, none at all, and clamped", () => {
    const circle = 2 * Math.PI * 20;
    expect(ringOffset(0, 20)).toBeCloseTo(circle);
    expect(ringOffset(1, 20)).toBeCloseTo(0);
    expect(ringOffset(0.5, 20)).toBeCloseTo(circle / 2);
    expect(ringOffset(7, 20)).toBeCloseTo(0);
    expect(ringOffset(-1, 20)).toBeCloseTo(circle);
  });
});

describe("nowLine", () => {
  const status = (phase: AutopilotStatus["phase"], steps: boolean[] = [], reason?: string): AutopilotStatus => ({
    phase,
    reason,
    plan: { steps: steps.map((d, i) => ({ text: `step ${i}`, done: d })), done: steps.filter(Boolean).length, total: steps.length },
    plan_path: "p",
  });
  it("says what the builder is on, or why it is not", () => {
    expect(nowLine(status("running", [true, false]))).toContain("Now: step 1");
    expect(nowLine(status("awaiting_approval"))).toContain("waiting for you");
    expect(nowLine(status("planning"))).toContain("writing its plan");
    expect(nowLine(status("paused", [true, false]))).toBe("Paused. Next: step 1.");
    expect(nowLine(status("done", [true]))).toBe("Every step of the plan is done.");
    expect(nowLine(status("stuck", [false], "Went round in circles."))).toBe("Went round in circles.");
  });
});
