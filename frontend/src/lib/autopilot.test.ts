// Unit: Autopilot's words and sums (autopilot.ts).

import { describe, expect, it } from "vitest";
import {
  compactTokens,
  controlLabel,
  controlsFor,
  currentStep,
  elapsedText,
  limitChip,
  needsPerson,
  phaseLabel,
  planProgress,
  type AutopilotPhase,
  type AutopilotStatus,
} from "./autopilot";

const status = (done: boolean[], phase: AutopilotPhase = "running"): AutopilotStatus => ({
  phase,
  plan: {
    steps: done.map((d, i) => ({ text: `step ${i}`, done: d })),
    done: done.filter(Boolean).length,
    total: done.length,
  },
  plan_path: ".cuttlefish/artifacts/plan.md",
});

describe("phaseLabel and needsPerson", () => {
  it("names every phase", () => {
    const phases: AutopilotPhase[] = ["off", "planning", "awaiting_approval", "running", "paused", "done", "stopped", "limit", "stuck"];
    expect(new Set(phases.map(phaseLabel)).size).toBe(phases.length);
  });
  it("calls for a person only when something waits on one", () => {
    expect(needsPerson("awaiting_approval")).toBe(true);
    expect(needsPerson("stuck")).toBe(true);
    expect(needsPerson("limit")).toBe(true);
    expect(needsPerson("running")).toBe(false);
    expect(needsPerson("done")).toBe(false);
  });
});

describe("controlsFor", () => {
  it("always offers Stop while it is live, and Pause only when running", () => {
    expect(controlsFor("running")).toEqual(["pause", "nudge", "stop"]);
    expect(controlsFor("awaiting_approval")).toEqual(["approve", "stop"]);
    expect(controlsFor("planning")).toEqual(["stop"]);
    expect(controlsFor("paused")).toEqual(["resume", "stop"]);
  });
  it("lets a person carry on after a limit or being stuck", () => {
    expect(controlsFor("limit")).toContain("resume");
    expect(controlsFor("stuck")).toContain("resume");
    expect(controlLabel("resume", "stuck")).toBe("Carry on");
    expect(controlLabel("resume", "paused")).toBe("Resume");
  });
  it("offers nothing once it has ended or was never on", () => {
    expect(controlsFor("done")).toEqual([]);
    expect(controlsFor("stopped")).toEqual([]);
    expect(controlsFor("off")).toEqual([]);
  });
});

describe("numbers in words", () => {
  it("shortens tokens", () => {
    expect(compactTokens(420)).toBe("420");
    expect(compactTokens(850_000)).toBe("850k");
    expect(compactTokens(3_200_000)).toBe("3.2M");
    expect(compactTokens(24_000_000)).toBe("24M");
  });
  it("says how long it ran", () => {
    expect(elapsedText(20)).toBe("under a minute");
    expect(elapsedText(45 * 60)).toBe("45 m");
    expect(elapsedText(6 * 3600 + 12 * 60)).toBe("6 h 12 m");
  });
});

describe("plan progress", () => {
  it("is the share ticked, and 0 with no plan", () => {
    expect(planProgress(status([true, true, false, false]))).toBe(0.5);
    expect(planProgress({ phase: "off", plan: null, plan_path: "" })).toBe(0);
  });
  it("points at the first step still to do", () => {
    expect(currentStep(status([true, false, false]))).toBe(1);
    expect(currentStep(status([true, true]))).toBeNull();
    expect(currentStep({ phase: "off", plan: null, plan_path: "" })).toBeNull();
  });
});

describe("waiting for a usage limit (CUT-299)", () => {
  it("is its own phase with a way to carry on now, and does not demand a person", () => {
    expect(phaseLabel("limit_wait")).toBe("Waiting for the usage limit");
    expect(needsPerson("limit_wait")).toBe(false);
    expect(controlsFor("limit_wait")).toEqual(["resume", "stop"]);
    expect(controlLabel("resume", "limit_wait")).toBe("Carry on now");
    expect(controlLabel("resume", "limit")).toBe("Carry on");
  });

  it("words a usage-limit wait with the time it continues and a countdown", () => {
    const now = new Date(2026, 10, 12, 10, 0, 0);
    const at = new Date(2026, 10, 12, 12, 14, 0);
    const wait = { ...status([false], "limit_wait"), resume_at: at.toISOString(), limit_kind: "window" as const };
    expect(limitChip(wait, now)).toEqual({
      text: "Waiting for usage limit, continues 12:14",
      countdown: "in 2 h 14 m",
      needsYou: false,
    });
    expect(limitChip({ ...wait, limit_kind: "backoff" }, now)?.text).toBe("Provider busy, retrying 12:14");
    expect(limitChip({ ...wait, resume_at: new Date(2026, 10, 12, 10, 0, 20).toISOString() }, now)?.countdown).toBe(
      "any moment",
    );
    const days = limitChip({ ...wait, resume_at: new Date(2026, 10, 16, 9, 0, 0).toISOString() }, now);
    expect(days?.text).toMatch(/^Waiting for usage limit, continues .*09:00$/);
    expect(days?.text).not.toBe("Waiting for usage limit, continues 09:00");
  });

  it("says plainly when the reset time is not known and a person has to act", () => {
    const now = new Date(2026, 10, 12, 10, 0, 0);
    const unknown = { ...status([false], "running"), limit_kind: "window" as const, resume_at: null };
    expect(limitChip(unknown, now)).toEqual({
      text: "Usage limit reached, reset unknown, waiting for you",
      countdown: null,
      needsYou: true,
    });
    expect(limitChip(status([false], "running"), now)).toBeNull();
    expect(limitChip({ ...status([false], "limit_wait"), resume_at: "nonsense" }, now)).toBeNull();
  });
});
