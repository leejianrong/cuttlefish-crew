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

/** A role the daemon still reports as working while a request for a person (a command to approve
 * or a question) holds it: it is paused for you, not busy. `blocked` requests are the stuck case,
 * which already has its own wording. */
export function waitsForYou(
  status: RoleStatus,
  requests: readonly { kind: string; role: string | null }[],
  role: string,
): boolean {
  return status === "working" && requests.some((r) => r.role === role && r.kind !== "blocked");
}

/** Codex and Claude Code run a round as one call and hand their token count back when it ends
 * (known-gaps.md), so a figure of 0 mid-round means "not reported yet", not "nothing used". */
export function reportsUsageAtRoundEnd(backend: string | null | undefined): boolean {
  return backend === "codex" || backend === "claude-code";
}
