// Words and small calculations for the Needs-you cards. The daemon sends data (a command, a rule,
// seconds left); what a person reads is decided here.

import type { NeedsYouRequest, RequestResolution } from "./api";

/** Time left as m:ss, never negative. */
export function formatRemaining(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(whole / 60);
  return `${minutes}:${String(whole % 60).padStart(2, "0")}`;
}

/** Seconds left for a pending request, counted from when it was fetched. */
export function secondsLeft(request: NeedsYouRequest, fetchedAt: number, now: number): number {
  return Math.max(0, (request.expires_in_s ?? 0) - (now - fetchedAt) / 1000);
}

/** A rule typed as words back into the list the daemon wants. */
export function parseRule(text: string): string[] {
  return text.split(/\s+/).filter((word) => word.length > 0);
}

export function ruleText(rule: string[] | null | undefined): string {
  return (rule ?? []).join(" ");
}

/** Whether the edited rule is still a start of the command, so Always allow can be offered
 * without a round trip. The daemon checks it again; this only avoids an obvious mistake. */
export function isStartOfCommand(rule: string[], command: string): boolean {
  if (rule.length === 0) return false;
  const words = command.split(" ");
  return rule.length <= words.length && rule.every((word, i) => words[i] === word);
}

/** When an answer reaches the agent, as the card's tag says it. */
export function landsLabel(lands: NeedsYouRequest["lands"]): string {
  switch (lands) {
    case "now":
      return "Paused until you answer";
    case "end_of_turn":
      return "Delivered when this turn ends";
    case "next_round":
      return "Takes effect next round";
  }
}

export const OUTCOMES: Record<RequestResolution, string> = {
  allowed_once: "Allowed once",
  allowed_always: "Always allowed",
  denied: "Denied",
  expired: "Denied: no answer in time",
  cancelled: "Denied: the team was stopped",
  abandoned: "Dropped: the agent process ended",
};

export function outcomeLabel(request: NeedsYouRequest): string {
  const base = request.state === "pending" ? "Waiting" : OUTCOMES[request.state];
  return request.state === "allowed_always" && request.rule?.length
    ? `${base}: ${ruleText(request.rule)}`
    : base;
}

/** A refused or conflicting answer, in words the card can show under its buttons. */
export function answerProblem(status: number, detail: string): string {
  if (status === 409) return "This request already ended, so it can't be answered now.";
  if (status === 404) return "This request is gone. It may have ended while the page was open.";
  if (status === 422) return detail;
  return "Couldn't send that answer. Check that the daemon is still running.";
}

/** Total pending across the fleet, for a badge: nothing at zero, "9+" past nine. */
export function badgeText(count: number): string {
  if (count <= 0) return "";
  return count > 9 ? "9+" : String(count);
}

const BACKEND_NAMES: Record<string, string> = {
  kopicode: "kopicode",
  "claude-code": "Claude Code",
  codex: "Codex",
};

export function backendLabel(backend: string): string {
  return BACKEND_NAMES[backend] ?? backend;
}
