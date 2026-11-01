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

/** Whether the edited rule is still the start of the command, or of one command in a chain
 * (`a && b`, `a | b`, `a; b`, one per line), so Always allow can be offered without a round trip.
 * Chains are split naively here, ignoring quotes; the daemon reads them properly and checks again,
 * so this only avoids an obvious mistake. */
export function isStartOfCommand(rule: string[], command: string): boolean {
  if (rule.length === 0) return false;
  return command.split(/&&|\|\||;|\||\n/).some((part) => {
    const words = part.trim().split(" ");
    return rule.length <= words.length && rule.every((word, i) => words[i] === word);
  });
}

/** When an answer reaches the agent, as the card's tag says it. */
export function landsLabel(lands: NeedsYouRequest["lands"], session = false): string {
  switch (lands) {
    case "now":
      return session ? "Paused until you answer here or in the terminal" : "Paused until you answer";
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
  superseded: "Ended: you steered it",
  answered: "Answered",
  declined: "Left unanswered: you declined",
};

/** How a request from a project's agent session in tmux ended. Nothing was denied when it expired or
 * the session stopped: the terminal's own dialog is still there, or the session is gone. */
const SESSION_OUTCOMES: Partial<Record<RequestResolution, string>> = {
  expired: "No answer in time: the terminal still asks",
  cancelled: "Ended: the agent session was stopped",
  abandoned: "Can no longer be answered here: it may still be open in the terminal",
  superseded: "Answered in the terminal",
};

/** How a `question` ended without an answer: nothing was denied, it simply went unanswered. */
const QUESTION_OUTCOMES: Partial<Record<RequestResolution, string>> = {
  expired: "Left unanswered: no answer in time",
  cancelled: "Left unanswered: the team was stopped",
};

/** How a `blocked` request ended: nothing was denied, there was nothing to answer. */
const BLOCKED_OUTCOMES: Partial<Record<RequestResolution, string>> = {
  cancelled: "Ended: the team was stopped",
  abandoned: "Ended: the team ended",
};

export function outcomeLabel(request: NeedsYouRequest): string {
  const blocked = request.kind === "blocked";
  const specific = request.session
    ? SESSION_OUTCOMES
    : blocked
      ? BLOCKED_OUTCOMES
      : request.kind === "question"
        ? QUESTION_OUTCOMES
        : {};
  const base =
    request.state === "pending"
      ? blocked
        ? "Needs a fix"
        : "Waiting"
      : (specific[request.state] ?? OUTCOMES[request.state]);
  if (request.state === "answered" && request.text) return `${base}: ${request.text}`;
  return request.state === "allowed_always" && request.rule?.length
    ? `${base}: ${ruleText(request.rule)}`
    : base;
}

/** A View-tier device can read Needs you but every answer route needs Approve (fleet/tiers.py), so
 * its buttons would only return a 403. */
export const VIEW_ONLY_NOTE =
  "This device can look but not answer. Answer on the PC, or ask the owner to raise this device to Approve.";

export function cannotAnswer(tier: string | null | undefined): boolean {
  return tier === "view";
}

/** A refused or conflicting answer, in words the card can show under its buttons. */
export function answerProblem(status: number, detail: string): string {
  if (status === 409) return "This request already ended, so it can't be answered now.";
  if (status === 404) return "This request is gone. It may have ended while the page was open.";
  if (status === 422 || status === 403) return detail;
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

/** The one queue's order (CUT-190). The daemon lists oldest first; this keeps that within each
 * band and puts the costliest first: an agent paused right now (`lands` is `now`) before one whose
 * answer is delivered later, and a Stuck card, which has nothing to answer, last. */
export function queueOrder(requests: readonly NeedsYouRequest[]): NeedsYouRequest[] {
  const band = (r: NeedsYouRequest) => (r.kind === "blocked" ? 2 : r.lands === "now" ? 0 : 1);
  return requests
    .map((request, index) => ({ request, index }))
    .sort((a, b) => band(a.request) - band(b.request) || a.index - b.index)
    .map(({ request }) => request);
}
