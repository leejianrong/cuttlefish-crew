import { describe, expect, it } from "vitest";
import type { LimitInfo } from "./api";
import {
  describeValue,
  inForce,
  overrideCount,
  parseLimit,
  sameLimits,
  withLimit,
} from "./limits";

function info(over: Partial<LimitInfo> = {}): LimitInfo {
  return {
    key: "max_turns",
    title: "Turns per round",
    summary: "",
    unit: "turns",
    minimum: 1,
    maximum: null,
    default: 100,
    zero_means: null,
    ...over,
  };
}

describe("inForce", () => {
  it("takes the first layer that sets it, then the default", () => {
    expect(inForce(info(), { max_turns: 10 }, { max_turns: 40 })).toBe(10);
    expect(inForce(info(), {}, { max_turns: 40 })).toBe(40);
    expect(inForce(info(), undefined, {})).toBe(100);
  });
  it("counts a zero as set", () => {
    expect(inForce(info({ minimum: 0 }), { max_turns: 0 }, { max_turns: 40 })).toBe(0);
  });
});

describe("describeValue", () => {
  it("says what a zero means when it means something", () => {
    const budget = info({ minimum: 0, zero_means: "no limit" });
    expect(describeValue(budget, 0)).toBe("no limit");
    expect(describeValue(budget, 5000000)).toBe("5,000,000 turns");
    expect(describeValue(info(), 100)).toBe("100 turns");
  });
});

describe("parseLimit", () => {
  it("reads blank as inherit and whole numbers in range as values", () => {
    expect(parseLimit(info(), "  ")).toEqual({ kind: "inherit" });
    expect(parseLimit(info(), "40")).toEqual({ kind: "value", value: 40 });
  });
  it("says what is wrong in words", () => {
    expect(parseLimit(info(), "2.5")).toEqual({ kind: "invalid", problem: "Use a whole number." });
    expect(parseLimit(info(), "-3")).toEqual({ kind: "invalid", problem: "Use a whole number." });
    expect(parseLimit(info(), "0")).toEqual({ kind: "invalid", problem: "At least 1." });
    expect(parseLimit(info({ maximum: 95, minimum: 0 }), "96")).toEqual({
      kind: "invalid",
      problem: "At most 95.",
    });
  });
});

describe("withLimit and sameLimits", () => {
  it("sets and clears without touching the original", () => {
    const base = { max_turns: 10 };
    expect(withLimit(base, "max_idle_rounds", 2)).toEqual({ max_turns: 10, max_idle_rounds: 2 });
    expect(withLimit(base, "max_turns", null)).toEqual({});
    expect(base).toEqual({ max_turns: 10 });
  });
  it("compares by key, ignoring order and absent against empty", () => {
    expect(sameLimits({ a: 1, b: 2 }, { b: 2, a: 1 })).toBe(true);
    expect(sameLimits(undefined, {})).toBe(true);
    expect(sameLimits({ a: 1 }, { a: 2 })).toBe(false);
    expect(sameLimits({ a: 1 }, {})).toBe(false);
    expect(overrideCount({ a: 1, b: 2 })).toBe(2);
    expect(overrideCount(undefined)).toBe(0);
  });
});
