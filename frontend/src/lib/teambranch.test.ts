import { describe, expect, it } from "vitest";
import type { EpisodicEventView } from "./api";
import { mergeLabel, teamBranch } from "./teambranch";

const at = (seq: number, event_type: string, payload: Record<string, unknown>): EpisodicEventView => ({
  seq,
  ts: "2026-11-01T00:00:00Z",
  event_type,
  payload,
});

const made = at(2, "TeamBranchMade", {
  branch: "cuttlefish/team-t1",
  roles: { dev: "cuttlefish/team-t1-dev", docs: "cuttlefish/team-t1-docs" },
});

describe("teamBranch", () => {
  it("is null for a team with no branch", () => {
    expect(teamBranch([at(1, "TaskSubmitted", { text: "x" })])).toBeNull();
  });

  it("lists each role as working until its merge is reported", () => {
    const result = teamBranch([made]);
    expect(result?.branch).toBe("cuttlefish/team-t1");
    expect(result?.roles.map((r) => [r.role, r.status])).toEqual([
      ["dev", "pending"],
      ["docs", "pending"],
    ]);
  });

  it("names the conflicted role and its files, and keeps the latest result per role", () => {
    const result = teamBranch([
      made,
      at(5, "RoleMergeReported", { role: "dev", status: "conflict", files: ["a.py"] }),
      at(6, "RoleMergeReported", { role: "docs", status: "merged", files: [] }),
      at(9, "RoleMergeReported", { role: "dev", status: "conflict", files: ["a.py", "b.py"] }),
    ]);
    expect(result?.roles[0]).toMatchObject({ role: "dev", status: "conflict", files: ["a.py", "b.py"] });
    expect(result?.roles[1].status).toBe("merged");
  });

  it("ignores merges from an earlier team branch", () => {
    const result = teamBranch([
      at(1, "RoleMergeReported", { role: "dev", status: "conflict", files: ["old.py"] }),
      made,
    ]);
    expect(result?.roles[0].status).toBe("pending");
  });

  it("words each status", () => {
    expect(mergeLabel("conflict")).toBe("Conflict");
    expect(mergeLabel("nothing")).toBe("No changes");
  });
});
