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
  | "stuck";

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
    default:
      return "Off";
  }
}

/** Whether the pill should draw attention: something is waiting on a person. */
export function needsPerson(phase: AutopilotPhase): boolean {
  return phase === "awaiting_approval" || phase === "stuck" || phase === "limit";
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
      return phase === "paused" ? "Resume" : "Carry on";
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

/** What the plan card tells a person who is not happy with the plan: Autopilot does not edit it. */
export const REVISE_HINT =
  "To change the plan, say so in the message box below, or edit the plan file; approve when it is right.";
