import { describe, expect, it } from "vitest";
import type { DiffFile } from "./api";
import { diffSummary, lineNumber, noLinesNote, sourceNote, statusLabel } from "./diff";

const file = (over: Partial<DiffFile> = {}): DiffFile => ({
  path: "a.txt",
  status: "modified",
  additions: 1,
  deletions: 0,
  binary: false,
  hunks: [{ header: "", old_start: 1, new_start: 1, lines: [{ type: "+", text: "x", new: 1 }] }],
  ...over,
});

describe("diff card text (CUT-289)", () => {
  it("summarises files and counts, and says when there is nothing", () => {
    expect(diffSummary({ files: [file()], additions: 12, deletions: 4 })).toBe("1 file, +12 −4");
    expect(diffSummary({ files: [file(), file()], additions: 1, deletions: 0 })).toBe("2 files, +1 −0");
    expect(diffSummary({ files: [] })).toBe("No changes yet");
  });

  it("labels a status and leaves a plain edit unlabelled", () => {
    expect(statusLabel(file({ status: "untracked" }))).toBe("new, not committed");
    expect(statusLabel(file())).toBeNull();
  });

  it("says why a file has no lines, and nothing when it has some", () => {
    expect(noLinesNote(file())).toBeNull();
    expect(noLinesNote(file({ withheld: "secret", hunks: [] }))).toMatch(/secret/);
    expect(noLinesNote(file({ binary: true, hunks: [] }))).toMatch(/Binary/);
    expect(noLinesNote(file({ omitted: true, hunks: [] }))).toMatch(/Too large/);
  });

  it("says where the changes come from", () => {
    expect(sourceNote({ mode: "worktree", dirty: true, base: "main" })).toMatch(/uncommitted work against main/);
    expect(sourceNote({ mode: "branch", base: "main" })).toMatch(/worktree has been removed/);
  });

  it("numbers a row by the side that exists", () => {
    expect(lineNumber({ old: 3 })).toBe("3");
    expect(lineNumber({ old: 3, new: 5 })).toBe("5");
  });
});
