// Unit: the schedule form's rules (schedule.ts).

import { describe, expect, it } from "vitest";
import { buildRecurrence, canAdd, describeRecurrence, nextLine, resultLine } from "./schedule";

describe("buildRecurrence", () => {
  it("writes the text the daemon reads", () => {
    expect(buildRecurrence("every-m", 30, "")).toBe("every 30m");
    expect(buildRecurrence("every-h", 2, "")).toBe("every 2h");
    expect(buildRecurrence("daily", 0, "06:30")).toBe("daily 06:30");
  });
  it("refuses what the daemon would", () => {
    expect(buildRecurrence("every-m", 0, "")).toBeNull();
    expect(buildRecurrence("every-h", 1.5, "")).toBeNull();
    expect(buildRecurrence("daily", 1, "24:00")).toBeNull();
    expect(buildRecurrence("daily", 1, "")).toBeNull();
  });
  it("needs a goal as well", () => {
    expect(canAdd("  ", "every 1h")).toBe(false);
    expect(canAdd("do it", null)).toBe(false);
    expect(canAdd("do it", "every 1h")).toBe(true);
  });
});

describe("describeRecurrence", () => {
  it("reads naturally and says UTC", () => {
    expect(describeRecurrence("every 1h")).toBe("Every hour");
    expect(describeRecurrence("every 15m")).toBe("Every 15 minutes");
    expect(describeRecurrence("daily 06:30")).toBe("Daily at 06:30 UTC");
  });
});

describe("nextLine", () => {
  const now = new Date("2026-05-01T08:00:00Z");
  it("says when, relative and absolute", () => {
    expect(nextLine({ enabled: true, next_due: "2026-05-01T08:12:00+00:00" }, now)).toBe(
      "08:12 UTC, in 12 minutes",
    );
    expect(nextLine({ enabled: true, next_due: "2026-05-01T11:00:00+00:00" }, now)).toBe(
      "11:00 UTC, in 3 hours",
    );
    expect(nextLine({ enabled: true, next_due: "2026-05-01T07:59:00+00:00" }, now)).toBe(
      "07:59 UTC, due now",
    );
  });
  it("has no next run while paused", () => {
    expect(nextLine({ enabled: false, next_due: "2026-05-01T08:12:00+00:00" }, now)).toBe("Paused");
  });
});

describe("resultLine", () => {
  it("is empty before a first run", () => {
    expect(resultLine({ last_run: null, last_result: null })).toBeNull();
  });
  it("says started, or why not", () => {
    const run = "2026-05-01T08:00:00+00:00";
    expect(resultLine({ last_run: run, last_result: "started" })).toBe(
      "Started the goal at 2026-05-01 08:00 UTC.",
    );
    expect(
      resultLine({ last_run: run, last_result: "skipped: the builder is still on its previous goal" }),
    ).toBe("Not started at 2026-05-01 08:00 UTC: the builder is still on its previous goal.");
  });
});
