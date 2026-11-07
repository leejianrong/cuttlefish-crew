import { describe, expect, it } from "vitest";
import type { ManagerBudget } from "./api";
import { managerBudgetLine } from "./managerbudget";

const base: ManagerBudget = {
  metered: true,
  tokens_used: 1200,
  turns_used: 7,
  max_tokens: null,
  max_turns: null,
  paused: false,
  error: null,
};

describe("managerBudgetLine", () => {
  it("says there is no dollar figure when no budget is set", () => {
    expect(managerBudgetLine(base)).toBe(
      "Today: 1,200 tokens, 7 turns. No daily budget is set; it reports tokens and turns, not dollars.",
    );
  });
  it("names both ceilings and the pause", () => {
    const line = managerBudgetLine({ ...base, max_tokens: 1000, max_turns: 50, paused: true });
    expect(line).toContain("of 1,000 tokens and 50 turns");
    expect(line).toContain("It is paused");
  });
  it("is honest about an unreadable backend and a bad setting", () => {
    expect(managerBudgetLine({ ...base, metered: false })).toContain("not readable");
    expect(managerBudgetLine({ ...base, error: "X must be a number" })).toContain("X must be");
  });
});
