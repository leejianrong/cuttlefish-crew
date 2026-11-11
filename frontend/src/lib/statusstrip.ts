// The project page's one-line status strip (CUT-286): what is running, how it last came up, what
// it has used and how long, the branch with its diff stat, and the pull request. Pure, so each part
// is unit-tested; the component only draws what this returns and keeps nothing of its own.
import type { AgentSession, Goal, GoalDiffResult, PrStatusResult, RoleUsage } from "./api";
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
  /** Hover text: which checks are failing. */
  title?: string;
}

export interface StripInput {
  session: AgentSession | null;
  /** The team, for a project that runs rounds rather than a builder session. */
  teamRunning: boolean;
  usage: Record<string, RoleUsage>;
  autopilot: AutopilotStatus | null;
  goal: Goal | null;
  stat: GoalDiffResult | null;
  /** The pull request as gh reports it; null until read or when there is none to report. */
  pr?: PrStatusResult | null;
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
    const pr = prNote(input.pr);
    parts.push({
      key: "pr",
      text: pr.text ? `Pull request: ${pr.text}` : "Pull request",
      tone: pr.attention ? "attention" : "plain",
      href: goal.pr_url,
      title: pr.title,
    });
  }
  return parts;
}

/** What gh says about the pull request in a few words: open, draft, merged or closed, then its
 * checks and review. Nothing when gh could not say (no gh, not logged in), so the link stands alone. */
export function prNote(pr: PrStatusResult | null | undefined): {
  text: string;
  attention: boolean;
  title?: string;
} {
  if (!pr?.ok || !pr.state) return { text: "", attention: false };
  const words: string[] = [pr.state === "open" && pr.draft ? "draft" : pr.state];
  const failing = pr.checks.filter((c) => c.state === "fail").map((c) => c.name);
  if (pr.state === "open") {
    if (failing.length > 0) words.push(`${failing.length} of ${pr.checks.length} checks failing`);
    else if (pr.checks_state === "pending") words.push("checks running");
    else if (pr.checks_state === "passing") words.push("checks passing");
    if (pr.review === "changes_requested") words.push("changes requested");
    else if (pr.review === "approved") words.push("approved");
  }
  const attention = pr.state === "open" && (failing.length > 0 || pr.review === "changes_requested");
  return {
    text: words.join(", "),
    attention,
    title: failing.length > 0 ? `Failing: ${failing.join(", ")}` : undefined,
  };
}
