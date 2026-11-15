// Autopilot's words and sums for the Chat tab's plan rail and plan card (CUT-174, ADR-0044). Pure,
// so each is unit-tested; the daemon decides everything, this only says it.

export type AutopilotPhase =
  | "off"
  | "planning"
  | "awaiting_approval"
  | "running"
  | "paused"
  | "done"
  | "stopped"
  | "limit"
  | "stuck"
  /** CUT-299: the builder hit a usage limit; Autopilot continues it at `resume_at`. */
  | "limit_wait"
  /** CUT-312: running, parked on a decision or a terminal dialog only a person can answer. */
  | "waiting_on_you";

export type WaitKind = "plan" | "decision" | "blocked" | "";

export interface PlanStep {
  text: string;
  done: boolean;
}

export interface AutopilotStatus {
  phase: AutopilotPhase;
  goal?: string;
  backend?: string;
  reason?: string;
  approved_at?: string | null;
  /** In `limit_wait`: when Autopilot will continue (ISO 8601, UTC). */
  resume_at?: string | null;
  /** CUT-311: what is being waited out (`window` or `backoff`) and where the signal came from. */
  limit_kind?: "window" | "backoff" | null;
  limit_source?: "hook" | "board" | "backoff" | null;
  /** CUT-312: what a person is being waited for and how long, in `awaiting_approval` and `waiting_on_you`. */
  waiting?: { kind: WaitKind; since: string; age_s: number } | null;
  /** Seconds parked on a person; Autopilot's hours do not count them. */
  parked_s?: number;
  /** Whether this project continues after a usage limit resets (it can opt out). */
  continue_after_reset?: boolean;
  elapsed_s?: number | null;
  nudges?: number;
  nudges_since_progress?: number;
  tokens_used?: number | null;
  limits?: { hours: number; tokens: number; nudges: number };
  plan: { steps: PlanStep[]; done: number; total: number } | null;
  plan_path: string;
}

export interface AutopilotOptions {
  hours?: number;
  tokens?: number;
  nudges?: number;
}

/** The state in a person's words: a short label for a pill. */
export function phaseLabel(phase: AutopilotPhase): string {
  switch (phase) {
    case "planning":
      return "Planning";
    case "awaiting_approval":
      return "Plan waiting for you";
    case "running":
      return "Running on its own";
    case "paused":
      return "Paused";
    case "done":
      return "Done";
    case "stopped":
      return "Stopped";
    case "limit":
      return "Limit reached";
    case "stuck":
      return "Stuck";
    case "limit_wait":
      return "Waiting for the usage limit";
    case "waiting_on_you":
      return "Waiting for you";
    default:
      return "Off";
  }
}

/** Whether the pill should draw attention: something is waiting on a person. */
export function needsPerson(phase: AutopilotPhase): boolean {
  return phase === "awaiting_approval" || phase === "stuck" || phase === "limit" || phase === "waiting_on_you";
}

export type Control = "approve" | "pause" | "resume" | "stop" | "nudge";

/** Which of the rail's buttons make sense now. Pause and Stop are offered whenever it is live. */
export function controlsFor(phase: AutopilotPhase): Control[] {
  switch (phase) {
    case "planning":
      return ["stop"];
    case "awaiting_approval":
      return ["approve", "stop"];
    case "running":
      return ["pause", "nudge", "stop"];
    case "paused":
    case "stuck":
    case "limit":
    case "limit_wait":
    case "waiting_on_you":
      return ["resume", "stop"];
    default:
      return [];
  }
}

export function controlLabel(control: Control, phase: AutopilotPhase): string {
  switch (control) {
    case "approve":
      return "Approve the plan";
    case "pause":
      return "Pause";
    case "resume":
      return phase === "paused" ? "Resume" : phase === "limit_wait" ? "Carry on now" : "Carry on";
    case "stop":
      return "Stop Autopilot";
    default:
      return "Nudge now";
  }
}

/** `3.2M`, `850k`, `420`: tokens, compactly. */
export function compactTokens(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(count >= 10_000_000 ? 0 : 1)}M`;
  if (count >= 1000) return `${Math.round(count / 1000)}k`;
  return String(count);
}

/** `6 h 12 m`, `45 m`, `under a minute`: how long it has run. */
export function elapsedText(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return "under a minute";
  const hours = Math.floor(minutes / 60);
  return hours > 0 ? `${hours} h ${minutes % 60} m` : `${minutes} m`;
}

/** The plan's progress as a fraction 0 to 1, for the bar. */
export function planProgress(status: AutopilotStatus): number {
  const plan = status.plan;
  return plan && plan.total > 0 ? plan.done / plan.total : 0;
}

/** The first step still to do, or null. */
export function currentStep(status: AutopilotStatus): number | null {
  const index = status.plan?.steps.findIndex((step) => !step.done) ?? -1;
  return index >= 0 ? index : null;
}

export interface LimitChip {
  /** The chip's words, without the countdown. */
  text: string;
  /** `in 2 h 14 m`, or null when there is no time to count to. */
  countdown: string | null;
  /** A person has to act: there is no reset time to wait for. */
  needsYou: boolean;
}

/** `14:05`, with the weekday and date when it is not today (a weekly window resets days away). */
export function clockText(at: Date, now: Date): string {
  const time = at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  if (at.toDateString() === now.toDateString()) return time;
  return `${at.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })} ${time}`;
}

/** The chip for a usage-limit wait (CUT-311), or null. The daemon holds the time; this words it. */
export function limitChip(status: AutopilotStatus, now: Date): LimitChip | null {
  if (status.phase === "limit_wait" && status.resume_at) {
    const at = new Date(status.resume_at);
    if (Number.isNaN(at.getTime())) return null;
    const seconds = Math.max(0, Math.round((at.getTime() - now.getTime()) / 1000));
    const lead = status.limit_kind === "backoff" ? "Provider busy, retrying" : "Waiting for usage limit, continues";
    return {
      text: `${lead} ${clockText(at, now)}`,
      countdown: seconds < 60 ? "any moment" : `in ${elapsedText(seconds)}`,
      needsYou: false,
    };
  }
  if (status.phase === "running" && status.limit_kind === "window" && !status.resume_at) {
    return { text: "Usage limit reached, reset unknown, waiting for you", countdown: null, needsYou: true };
  }
  return null;
}

export interface WaitingChip {
  text: string;
  /** `waiting 12 m`: how long, counted from when the daemon says it began. */
  age: string;
  /** What to do, and for a terminal dialog the honest limit of this page. */
  hint: string | null;
}

/** The chip for a wait on a person (CUT-312), or null. `kinds` picks which waits to word. */
export function waitingChip(
  status: AutopilotStatus,
  now: Date,
  kinds: WaitKind[] = ["decision", "blocked"],
): WaitingChip | null {
  const waiting = status.waiting;
  if (!waiting || !kinds.includes(waiting.kind)) return null;
  const since = new Date(waiting.since);
  const age = Number.isNaN(since.getTime())
    ? waiting.age_s
    : Math.max(0, (now.getTime() - since.getTime()) / 1000);
  const age_text = `waiting ${elapsedText(age)}`;
  switch (waiting.kind) {
    case "blocked":
      return {
        text: "The session is waiting on a dialog in its terminal",
        age: age_text,
        hint: "A terminal dialog can only be answered in the terminal. Nothing here can answer it.",
      };
    case "decision":
      return {
        text: "Waiting for your decision",
        age: age_text,
        hint: "Answer the card under Needs you. Autopilot's hours do not count this wait.",
      };
    case "plan":
      return { text: "The plan is waiting for your approval", age: age_text, hint: null };
    default:
      return null;
  }
}

/** What the plan card tells a person who is not happy with the plan: Autopilot does not edit it. */
export const REVISE_HINT =
  "To change the plan, say so in the message box below, or edit the plan file; approve when it is right.";
