import { describe, expect, it } from "vitest";
import type { DiffFile, DiffLine } from "./api";
import { annotationMessage, canAnnotate, diffMoved, lineRef, MAX_NOTE_CHARS, sideOf, tooLong } from "./annotate";

const file = (over: Partial<DiffFile> = {}): DiffFile => ({
  path: "src/calc.py",
  status: "modified",
  additions: 1,
  deletions: 1,
  binary: false,
  hunks: [],
  ...over,
});
const added: DiffLine = { type: "+", text: "def mul(a, b):", new: 9 };
const removed: DiffLine = { type: "-", text: "return a - b", old: 6 };
const context: DiffLine = { type: " ", text: "x", old: 1, new: 1 };

describe("annotating a diff line (CUT-291)", () => {
  it("anchors an added line on the new side and a removed one on the old", () => {
    expect(sideOf(added)).toBe("new");
    expect(sideOf(removed)).toBe("old");
    expect(lineRef(file(), added)).toEqual({ path: "src/calc.py", side: "new", line: 9, quote: "def mul(a, b):" });
    expect(lineRef(file(), removed)).toMatchObject({ side: "old", line: 6 });
  });

  it("only offers changed lines of files whose lines are really shown", () => {
    expect(canAnnotate(file(), added)).toBe(true);
    expect(canAnnotate(file(), removed)).toBe(true);
    expect(canAnnotate(file(), context)).toBe(false);
    expect(canAnnotate(file({ withheld: "secret" }), added)).toBe(false);
    expect(canAnnotate(file({ omitted: true }), added)).toBe(false);
    expect(canAnnotate(file({ binary: true }), added)).toBe(false);
  });

  it("quotes the line, says where it was seen, and cuts a very long quote", () => {
    const text = annotationMessage(lineRef(file(), added), "  Add a docstring.  ", {
      branch: "cuttlefish/x",
      head: "0123456789abcdef",
      dirty: true,
    });
    expect(text).toBe(
      "Review note on src/calc.py, line 9 (added), as seen on cuttlefish/x at 01234567 with uncommitted changes:\n> def mul(a, b):\n\nAdd a docstring.",
    );
    const long = lineRef(file(), { type: "+", text: "x".repeat(500), new: 1 });
    expect(long.quote.length).toBe(241);
    expect(annotationMessage(lineRef(file(), removed), "n", {})).toContain("line 6 before your change (removed)");
  });

  it("notices that the branch moved since the diff was read", () => {
    const read = { head: "a", files_changed: 2, additions: 5, deletions: 1 };
    expect(diffMoved(read, { ...read })).toBe(false);
    expect(diffMoved(read, { ...read, head: "b" })).toBe(true);
    expect(diffMoved(read, { ...read, additions: 6 })).toBe(true);
  });

  it("refuses a message over the daemon's limit before sending", () => {
    expect(tooLong("a".repeat(MAX_NOTE_CHARS))).toBeNull();
    expect(tooLong("a".repeat(MAX_NOTE_CHARS + 1))).toMatch(/at most 8000/);
  });
});
