// What a role's status means to a person, in one place so the chip, the sprite and the note agree.

import type { ProjectBudget, RoleStatus, RoleUsage } from "./api";
import type { RoundOutcome } from "./events";

/** A blocked role is waiting for a decision only with a review gate or a usage limit (ADR-0016,
 * ADR-0017). Otherwise the round just ended and the task finishes after a short wait for a steer
 * message (ADR-0008): offering Approve and Reject then offers a decision nobody is waiting for. */
export function awaitingDecision(
  requireApproval: boolean,
  usage: RoleUsage,
  budget: ProjectBudget,
): boolean {
  const tokensOver = budget.max_tokens !== null && usage.tokens >= budget.max_tokens;
  const costOver =
    budget.max_cost_usd !== null && usage.cost_usd !== null && usage.cost_usd >= budget.max_cost_usd;
  return requireApproval || tokensOver || costOver;
}

/** A round that finished well leaves the role "blocked" for a few seconds, in case a person wants
 * to add to it (ADR-0008). That is the task ending, not something needing anyone: show it calm. */
export function isFinishing(
  status: RoleStatus,
  lastRound: RoundOutcome | null,
  held: boolean,
  awaiting: boolean,
): boolean {
  return status === "blocked" && !awaiting && !held && lastRound?.kind === "completed";
}
