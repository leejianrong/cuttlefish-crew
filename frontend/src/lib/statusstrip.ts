// The project page's one-line status strip (CUT-286): what is running, how it last came up, what
// it has used and how long, the branch with its diff stat, and the pull request. Pure, so each part
// is unit-tested; the component only draws what this returns and keeps nothing of its own.
import type { AgentSession, Goal, GoalDiffResult, RoleUsage } from "./api";
import { backendLabel } from "./agentsession";
import { compactTokens, elapsedText, needsPerson, phaseLabel, type AutopilotStatus } from "./autopilot";
import { statText } from "./diff";
import { eventWhen } from "./overview";

export interface StripPart {
  key: string;
  text: string;
  tone: "ok" | "attention" | "plain";
  /** A link, for the pull request. */
  href?: string;
  mono?: boolean;
}

export interface StripInput {
  session: AgentSession | null;
  /** The team, for a project that runs rounds rather than a builder session. */
  teamRunning: boolean;
  usage: Record<string, RoleUsage>;
  autopilot: AutopilotStatus | null;
  goal: Goal | null;
  stat: GoalDiffResult | null;
}

const LAST_ACTION: Record<string, string> = {
  started: "Started",
  reattached: "Reattached",
  relaunched: "Relaunched",
};

/** The strip's parts in order, or none when the project has never been given work (it then shows
 * nothing rather than a row of "idle"). */
export function stripParts(input: StripInput, now = new Date()): StripPart[] {
  const { session, teamRunning, autopilot, goal, stat } = input;
  const live = session?.state === "running";
  if (!live && !teamRunning && !goal && session?.state !== "stopped") return [];
  const parts: StripPart[] = [];
  if (live) parts.push({ key: "state", text: `${backendLabel(session.backend)} running`, tone: "ok" });
  else if (teamRunning) parts.push({ key: "state", text: "Team running", tone: "ok" });
  else parts.push({ key: "state", text: session?.state === "stopped" ? "Stopped" : "Idle", tone: "plain" });
  if (autopilot && autopilot.phase !== "off") {
    parts.push({
      key: "autopilot",
      text: `Autopilot: ${phaseLabel(autopilot.phase)}`,
      tone: needsPerson(autopilot.phase) ? "attention" : "plain",
    });
  }
  if (session?.last_action && session.at) {
    parts.push({
      key: "last",
      text: `${LAST_ACTION[session.last_action]} ${eventWhen(session.at, now)}`,
      tone: "plain",
    });
  }
  const teamTokens = Object.values(input.usage).reduce((sum, u) => sum + u.tokens, 0);
  const tokens = autopilot?.tokens_used ?? (teamTokens > 0 ? teamTokens : null);
  if (tokens) parts.push({ key: "tokens", text: `${compactTokens(tokens)} tokens`, tone: "plain" });
  if (autopilot?.elapsed_s != null && autopilot.phase !== "off") {
    parts.push({ key: "time", text: elapsedText(autopilot.elapsed_s), tone: "plain" });
  }
  if (goal?.branch) {
    const counts = stat?.ok && (stat.files_changed ?? 0) > 0 ? ` ${statText(stat)}` : "";
    const dirty = stat?.ok && stat.dirty ? " (uncommitted)" : "";
    parts.push({ key: "branch", text: `${goal.branch}${counts}${dirty}`, tone: "plain", mono: true });
  }
  if (goal?.pr_url) {
    parts.push({ key: "pr", text: "Pull request", tone: "plain", href: goal.pr_url });
  }
  return parts;
}
