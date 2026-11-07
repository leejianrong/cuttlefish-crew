import type { ManagerBudget } from "./api";

const n = (v: number): string => v.toLocaleString("en-US");

/** One honest sentence about the manager's use today (CUT-151). Claude Code reports tokens and
 * turns, never dollars, so a cap is in those and the line never shows a cost. */
export function managerBudgetLine(b: ManagerBudget): string {
  if (b.error) return `Daily budget not applied: ${b.error}`;
  if (!b.metered) {
    return "Its use is not readable for this backend, so no daily budget is applied.";
  }
  const used = `Today: ${n(b.tokens_used ?? 0)} tokens, ${n(b.turns_used ?? 0)} turns`;
  const caps: string[] = [];
  if (b.max_tokens !== null) caps.push(`${n(b.max_tokens)} tokens`);
  if (b.max_turns !== null) caps.push(`${n(b.max_turns)} turns`);
  if (caps.length === 0) {
    return `${used}. No daily budget is set; it reports tokens and turns, not dollars.`;
  }
  const tail = b.paused
    ? "It is paused: wakes and check-ins are held."
    : "Wakes and check-ins stop at the budget.";
  return `${used} of ${caps.join(" and ")}. ${tail}`;
}
