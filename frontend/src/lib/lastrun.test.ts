// Unit: what the Overview says about a run that has ended (lastrun.ts).

import { describe, expect, it } from "vitest";
import type { EpisodicEventView } from "./api";
import { lastRun, modelWords, notRunText, runFigures, runHeading } from "./lastrun";

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

  it("carries what each role said last, from its completed delegation", () => {
    const said = [
      event("2026-10-10T10:00:00Z", "TaskSubmitted"),
      event("2026-10-10T10:03:00Z", "DelegationCompleted", { role: "builder", summary: "first try" }),
      event("2026-10-10T10:06:00Z", "DelegationCompleted", { role: "builder", summary: " Linux box 6.1 " }),
      event("2026-10-10T10:07:00Z", "DelegationCompleted", { role: "reviewer", summary: "" }),
    ];
    const run = lastRun(said, { builder: "done", reviewer: "done" }, usage);
    expect(run?.answers).toEqual([{ role: "builder", text: "Linux box 6.1" }]);
  });

  it("shows the model's words without the delegation's status string in front (CUT-109)", () => {
    expect(modelWords("kopicode finished with no edit needed (completed): Linux box 6.1")).toBe("Linux box 6.1");
    expect(modelWords("kopicode edited 2 file(s) (completed): added remove(): done: yes")).toBe("added remove(): done: yes");
    expect(modelWords("Codex edited 3 file(s): fixed it")).toBe("fixed it");
    expect(modelWords("Claude Code said this: and that")).toBe("Claude Code said this: and that");
    expect(modelWords("I ran uname: Linux")).toBe("I ran uname: Linux");
  });

  it("has no words to show when the summary is only the status string", () => {
    expect(modelWords("kopicode finished with no edit needed (completed)")).toBe("");
    expect(modelWords("Codex finished with no edit needed")).toBe("");
    expect(modelWords("Claude Code finished (success)")).toBe("");
    const bare = [
      event("2026-10-10T10:00:00Z", "TaskSubmitted"),
      event("2026-10-10T10:03:00Z", "DelegationCompleted", {
        role: "builder",
        summary: "kopicode finished with no edit needed (completed)",
      }),
    ];
    expect(lastRun(bare, { builder: "done" }, usage)?.answers).toEqual([]);
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

  it("describes a run whose other roles were never given a task, instead of staying silent (CUT-108)", () => {
    const run = lastRun(
      events,
      { planner: "failed", builder: "queued", tester: "queued", reviewer: "queued" },
      usage,
    );
    expect(run?.outcome).toBe("failed");
    expect(run?.notRun).toEqual(["builder", "tester", "reviewer"]);
    expect(notRunText(run!)).toBe(
      "builder, tester and reviewer were not started: a start runs only the roles given a task.",
    );
    expect(notRunText(lastRun(events, { builder: "done", tester: "queued" }, usage)!)).toBe(
      "tester was not started: a start runs only the roles given a task.",
    );
  });

  it("has nothing to say about roles when every role ran, or when none has started", () => {
    expect(notRunText(lastRun(events, { builder: "done", reviewer: "done" }, usage)!)).toBeNull();
    expect(lastRun(events, { builder: "queued", reviewer: "queued" }, usage)).toBeNull();
  });

  it("leaves out a cost no role reported", () => {
    const run = lastRun(events, { builder: "done" }, { builder: { tokens: 500, cost_usd: null } });
    expect(runFigures(run!)).toBe("2 files changed · 12 min · 500 tokens");
  });
});
