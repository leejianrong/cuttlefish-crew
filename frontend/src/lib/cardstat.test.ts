import { describe, expect, it } from "vitest";
import { cardStatLine } from "./cardstat";

describe("card stat line (CUT-287)", () => {
  it("says the counts, and flags uncommitted work", () => {
    expect(cardStatLine([3, 12, 4])).toEqual({ text: "3 files +12 −4", attention: false });
    expect(cardStatLine([1, 2, 0, 1])?.text).toBe("1 file +2 −0 (uncommitted)");
  });

  it("adds the pull request, and its checks only while it is open", () => {
    expect(cardStatLine([2, 5, 1, 0, "o", "f"])).toEqual({
      text: "2 files +5 −1 · PR open, checks failing",
      attention: true,
    });
    expect(cardStatLine([0, 0, 0, 0, "o", "r"])?.text).toBe("PR open, checks running");
    expect(cardStatLine([0, 0, 0, 0, "m", "p"])).toEqual({ text: "PR merged", attention: false });
    expect(cardStatLine([0, 0, 0, 0, "c"])?.text).toBe("PR closed");
  });

  it("shows nothing when there is nothing to say", () => {
    expect(cardStatLine(null)).toBeNull();
    expect(cardStatLine(undefined)).toBeNull();
    expect(cardStatLine([0, 0, 0])).toBeNull();
  });
});
