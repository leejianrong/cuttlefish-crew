import { describe, expect, it } from "vitest";
import { cardStatLine } from "./cardstat";

describe("card stat line (CUT-287)", () => {
  it("says the counts, and flags uncommitted work", () => {
    expect(cardStatLine({ f: 3, a: 12, d: 4 })).toEqual({ text: "3 files +12 −4", attention: false });
    expect(cardStatLine({ f: 1, a: 2, d: 0, u: 1 })?.text).toBe("1 file +2 −0 (uncommitted)");
  });

  it("adds the pull request, and its checks only while it is open", () => {
    expect(cardStatLine({ f: 2, a: 5, d: 1, p: "o", c: "f" })).toEqual({
      text: "2 files +5 −1 · PR open, checks failing",
      attention: true,
    });
    expect(cardStatLine({ p: "o", c: "r" })?.text).toBe("PR open, checks running");
    expect(cardStatLine({ p: "m", c: "p" })).toEqual({ text: "PR merged", attention: false });
    expect(cardStatLine({ p: "c" })?.text).toBe("PR closed");
  });

  it("shows nothing when there is nothing to say", () => {
    expect(cardStatLine(null)).toBeNull();
    expect(cardStatLine(undefined)).toBeNull();
    expect(cardStatLine({})).toBeNull();
  });
});
