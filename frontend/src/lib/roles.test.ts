import { describe, expect, it } from "vitest";
import { awaitingDecision, isFinishing } from "./roles";

const done = { kind: "completed", text: "ok" } as const;
const none = { max_tokens: null, max_cost_usd: null };

describe("awaitingDecision", () => {
  it("is a review gate or a usage limit, nothing else", () => {
    expect(awaitingDecision(false, { tokens: 5, cost_usd: null }, none)).toBe(false);
    expect(awaitingDecision(true, { tokens: 5, cost_usd: null }, none)).toBe(true);
    expect(awaitingDecision(false, { tokens: 10, cost_usd: null }, { ...none, max_tokens: 10 })).toBe(true);
    expect(awaitingDecision(false, { tokens: 1, cost_usd: 2 }, { ...none, max_cost_usd: 2 })).toBe(true);
    expect(awaitingDecision(false, { tokens: 1, cost_usd: null }, { ...none, max_cost_usd: 2 })).toBe(false);
  });
});

describe("isFinishing", () => {
  it("is a blocked role whose last round completed and nobody is waited on", () => {
    expect(isFinishing("blocked", done, false, false)).toBe(true);
  });
  it("is not when held, when a decision is awaited, or when the round did not complete", () => {
    expect(isFinishing("blocked", done, true, false)).toBe(false);
    expect(isFinishing("blocked", done, false, true)).toBe(false);
    expect(isFinishing("blocked", { kind: "failed", text: "x" }, false, false)).toBe(false);
    expect(isFinishing("blocked", null, false, false)).toBe(false);
    expect(isFinishing("working", done, false, false)).toBe(false);
  });
});
