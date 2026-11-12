import { describe, expect, it } from "vitest";
import { agentsText, diskText, driftText, loadText, pickerNote, warnings } from "./workerhealth";

const GIB = 1024 ** 3;

describe("worker health text", () => {
  it("says nothing for a machine that did not report", () => {
    expect(diskText(null)).toBe("");
    expect(loadText(undefined)).toBe("");
    expect(agentsText({})).toBe("");
    expect(warnings({ flags: [], health: null })).toEqual([]);
  });

  it("reads disk, load and teams", () => {
    const health = { disk_free: 1.5 * GIB, disk_total: 500 * GIB, load1: 2.5, cpus: 4, agents: 1 };
    expect(diskText(health)).toBe("1.5 GB free of 500 GB");
    expect(loadText(health)).toBe("load 2.5 on 4 cpus");
    expect(agentsText(health)).toBe("1 team running");
    expect(agentsText({ agents: 0 })).toBe("0 teams running");
  });

  it("turns flags into warnings and picker notes", () => {
    const health = { disk_free: 1 * GIB, disk_total: 100 * GIB };
    expect(warnings({ flags: ["disk"], health })).toEqual(["Disk nearly full (1 GB free of 100 GB)"]);
    expect(pickerNote({ online: true, flags: ["disk", "load"] })).toBe(" (disk low, busy)");
    expect(pickerNote({ online: true, flags: [] })).toBe("");
    expect(pickerNote({ online: false, flags: [] })).toBe(" (offline)");
  });

  it("names what differs from the worker profile", () => {
    expect(driftText({ drift: ["docker missing", "git 2.20 is older than 2.30"] })).toBe(
      "Differs from the worker profile: docker missing, git 2.20 is older than 2.30",
    );
    expect(driftText({ drift: [] })).toBe("");
    expect(driftText({ drift: null })).toBe("");
    expect(driftText({})).toBe("");
  });
});
