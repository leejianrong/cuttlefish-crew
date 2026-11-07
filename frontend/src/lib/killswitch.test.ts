import { describe, expect, it } from "vitest";
import { killResultText } from "./killswitch";

describe("killResultText", () => {
  it("says nothing was running when no project stopped anything", () => {
    expect(killResultText({})).toBe("Nothing was running.");
    expect(killResultText({ a: [] })).toBe("Nothing was running.");
  });

  it("names what was stopped and on how many projects", () => {
    expect(killResultText({ a: ["builder"] })).toBe("Stopped the builder on 1 project.");
    expect(killResultText({ a: ["team", "builder"], b: ["builder"], c: [] })).toBe(
      "Stopped the team and the builder on 2 projects.",
    );
  });
});
