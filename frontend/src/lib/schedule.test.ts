// Unit: the schedule form's rules (schedule.ts).

import { describe, expect, it } from "vitest";
import {
  buildRecurrence,
  canAdd,
  describeRecurrence,
  graceMinutes,
  graceLine,
  nextLine,
  precheckLine,
  resultLine,
} from "./schedule";

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

describe("weekday, weekly and cron recurrences", () => {
  const extra = { day: "mon", cron: "" };
  it("writes the text the daemon reads", () => {
    expect(buildRecurrence("weekdays", 0, "09:00", extra)).toBe("weekdays 09:00");
    expect(buildRecurrence("weekly", 0, "09:30", { ...extra, day: "fri" })).toBe("weekly fri 09:30");
    expect(buildRecurrence("cron", 0, "", { ...extra, cron: " 0  9 * * 1-5 " })).toBe("cron 0 9 * * 1-5");
    expect(buildRecurrence("cron", 0, "", { ...extra, cron: "*/15 * * * *" })).toBe("cron */15 * * * *");
  });
  it("refuses what the daemon would", () => {
    expect(buildRecurrence("weekdays", 0, "24:00", extra)).toBeNull();
    expect(buildRecurrence("weekly", 0, "09:00", { ...extra, day: "someday" })).toBeNull();
    expect(buildRecurrence("cron", 0, "", { ...extra, cron: "0 9 * *" })).toBeNull();
    expect(buildRecurrence("cron", 0, "", { ...extra, cron: "0 9 * * mon" })).toBeNull();
    expect(buildRecurrence("cron", 0, "", { ...extra, cron: "" })).toBeNull();
  });
  it("reads naturally and always says UTC", () => {
    expect(describeRecurrence("weekdays 09:00")).toBe("Weekdays at 09:00 UTC");
    expect(describeRecurrence("weekly mon 09:30")).toBe("Every Monday at 09:30 UTC");
    expect(describeRecurrence("cron 0 9 * * 1-5")).toBe("Cron 0 9 * * 1-5, UTC");
  });
});

describe("grace and precheck", () => {
  it("reads the grace field: blank is none, a whole number of minutes, else invalid", () => {
    expect(graceMinutes("")).toEqual({ ok: true, value: null });
    expect(graceMinutes(" 45 ")).toEqual({ ok: true, value: 45 });
    expect(graceMinutes("0").ok).toBe(false);
    expect(graceMinutes("1.5").ok).toBe(false);
    expect(graceMinutes("10081").ok).toBe(false);
  });
  it("describes them on the row", () => {
    expect(graceLine({ grace_minutes: 30 })).toBe("Skipped if it is reached more than 30 minutes late.");
    expect(graceLine({ grace_minutes: null })).toBeNull();
    expect(precheckLine({ precheck: "git diff --quiet" })).toBe("Runs only if git diff --quiet exits 0.");
    expect(precheckLine({ precheck: null })).toBeNull();
  });
  it("words a precheck skip as the precheck's", () => {
    const run = "2026-05-01T08:00:00+00:00";
    expect(resultLine({ last_run: run, last_result: "skipped by precheck: it exited 1" })).toBe(
      "Skipped by the precheck at 2026-05-01 08:00 UTC: it exited 1.",
    );
    expect(resultLine({ last_run: run, last_result: "skipped: missed by 45 minutes, past the 30 minute grace" })).toBe(
      "Not started at 2026-05-01 08:00 UTC: missed by 45 minutes, past the 30 minute grace.",
    );
  });
});
