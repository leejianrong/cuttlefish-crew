import { describe, expect, it } from "vitest";
import { parsePaths, shareNotes } from "./worktreeshare";

describe("worktree sharing (CUT-305)", () => {
  it("reads one path per line, trimmed and without repeats", () => {
    expect(parsePaths(" node_modules \n\nconfig/local.json, node_modules\n")).toEqual([
      "node_modules",
      "config/local.json",
    ]);
    expect(parsePaths("  \n")).toEqual([]);
  });

  it("says what was linked, copied and left out", () => {
    expect(
      shareNotes({
        linked: ["node_modules"],
        copied: ["a.json"],
        skipped: [{ path: ".env", why: "looks like a secret" }],
      }),
    ).toEqual(["Linked: node_modules", "Copied: a.json", "Left out .env: looks like a secret"]);
    expect(shareNotes(undefined)).toEqual([]);
  });
});
