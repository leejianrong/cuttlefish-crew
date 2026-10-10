// Unit: what the Overview says about a run that has ended (lastrun.ts).

import { describe, expect, it } from "vitest";
import type { EpisodicEventView } from "./api";
import { lastRun, runFigures, runHeading } from "./lastrun";

function event(
  ts: string,
  event_type: string,
  payload: Record<string, unknown> = {},
): EpisodicEventView {
  return { seq: 1, ts, event_type, payload };
}

const usage = { builder: { tokens: 41_200, cost_usd: 0.04 }, reviewer: { tokens: 800, cost_usd: 0.01 } };

describe("lastRun", () => {
  const events = [
    event("2026-10-10T10:00:00Z", "TaskSubmitted"),
    event("2026-10-10T10:06:00Z", "DelegationCompleted", { role: "builder", edited_paths: ["a.py", "b.py"] }),
    event("2026-10-10T10:12:00Z", "DelegationCompleted", { role: "reviewer", edited_paths: ["a.py"] }),
  ];

  it("counts each changed file once and adds up time and usage", () => {
    const run = lastRun(events, { builder: "done", reviewer: "done" }, usage);
    expect(run).toMatchObject({ outcome: "finished", files: 2, minutes: 12, tokens: 42_000 });
    expect(run?.cost_usd).toBeCloseTo(0.05);
    expect(runHeading(run!)).toBe("The last run finished");
    expect(runFigures(run!)).toBe("2 files changed · 12 min · 42k tokens · $0.05");
  });

  it("is failed when any role failed, and carries its reason", () => {
    const failed = [...events, event("2026-10-10T10:13:00Z", "TaskFailed", { role: "reviewer", error: "boom" })];
    const run = lastRun(failed, { builder: "done", reviewer: "failed" }, usage);
    expect(run?.outcome).toBe("failed");
    expect(run?.failures).toEqual([{ role: "reviewer", error: "boom" }]);
  });

  it("is stopped when a role was stopped and none failed", () => {
    expect(lastRun(events, { builder: "done", reviewer: "stopped" }, usage)?.outcome).toBe("stopped");
  });

  it("says nothing while a role is still working or waiting, or before anything ran", () => {
    expect(lastRun(events, { builder: "done", reviewer: "working" }, usage)).toBeNull();
    expect(lastRun(events, { builder: "done", reviewer: "blocked" }, usage)).toBeNull();
    expect(lastRun([], { builder: "queued" }, usage)).toBeNull();
  });

  it("leaves out a cost no role reported", () => {
    const run = lastRun(events, { builder: "done" }, { builder: { tokens: 500, cost_usd: null } });
    expect(runFigures(run!)).toBe("2 files changed · 12 min · 500 tokens");
  });
});
