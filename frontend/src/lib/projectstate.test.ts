// Unit: the one-word state of a project (projectstate.ts), including the stuck threshold.

import { describe, expect, it } from "vitest";
import type { ProjectSummary, RoleStatus } from "./api";
import { projectState, STUCK_AFTER_MS } from "./projectstate";

const NOW = Date.parse("2026-11-02T12:00:00Z");
const before = (ms: number) => new Date(NOW - ms).toISOString();
const MIN = 60_000;

function project(
  status: Record<string, RoleStatus>,
  over: { running?: boolean; approval?: boolean; ago?: number | null } = {},
): ProjectSummary {
  return {
    status,
    running: over.running ?? false,
    require_approval: over.approval ?? false,
    last_event_at: over.ago === null ? null : before(over.ago ?? 5 * MIN),
  } as unknown as ProjectSummary;
}

const state = (p: ProjectSummary, waiting = 0) => projectState(p, waiting, NOW);

describe("projectState", () => {
  it("is waiting when a request needs a person, whatever else is true", () => {
    expect(state(project({ builder: "working" }), 2)).toEqual({
      state: "waiting",
      reason: "2 requests need you",
    });
    expect(state(project({ builder: "failed" }), 1).reason).toBe("1 request needs you");
  });

  it("is waiting for a review only when the review gate is on", () => {
    expect(state(project({ builder: "blocked" }, { approval: true })).state).toBe("waiting");
    expect(state(project({ builder: "blocked" })).state).toBe("working");
  });

  it("is idle before anything has run, and after a stop", () => {
    expect(state(project({ builder: "queued" }, { ago: null }))).toEqual({
      state: "idle",
      reason: "Not started yet",
    });
    expect(state(project({ builder: "stopped" })).state).toBe("idle");
  });

  it("is stuck when a role failed", () => {
    expect(state(project({ builder: "failed", reviewer: "queued" }, { ago: 90 * MIN }))).toEqual({
      state: "stuck",
      reason: "builder failed 2 h ago",
    });
  });

  it("is working while recent, and stuck once quiet for the stated time", () => {
    const quiet = (ms: number) => state(project({ builder: "working" }, { ago: ms }));
    expect(quiet(STUCK_AFTER_MS - MIN)).toEqual({ state: "working", reason: "builder is working" });
    expect(quiet(STUCK_AFTER_MS)).toEqual({ state: "stuck", reason: "No progress for 3 h" });
  });

  it("is done when every role that started has finished", () => {
    expect(state(project({ builder: "done", reviewer: "queued" }, { ago: 26 * 60 * MIN }))).toEqual({
      state: "done",
      reason: "Finished 1 d ago",
    });
  });
});
