// Advisory notes on a context handoff (CUT-313, ADR-0042). Counts and word matches the daemon
// derived with no model call; they never say a handoff was good or bad, and nothing waits on them.

/** What the timeline attaches to a context refresh: what the builder did after it. */
export interface PostBoundary {
  repeated_commands?: number;
  repeated_edits?: number;
  plan_ticked?: boolean | null;
  recovery_s?: number | null;
  nudges?: number;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

const NATIVE = "No handoff document was asked for: Claude Code compacted its own context.";

/** A native compaction leaves no document to read, so only what followed it is noted. */
export function refreshNote(after: PostBoundary | null | undefined): string {
  if (!after) return NATIVE;
  const facts: string[] = [];
  if (after.repeated_commands) facts.push(plural(after.repeated_commands, "repeated command"));
  if (after.repeated_edits) facts.push(plural(after.repeated_edits, "repeated edit"));
  if (after.plan_ticked === false) facts.push("plan not touched since");
  if (typeof after.recovery_s === "number") {
    facts.push(`first commit or plan edit ${Math.max(1, Math.round(after.recovery_s / 60))} min later`);
  }
  if (after.nudges) facts.push(plural(after.nudges, "nudge"));
  return facts.length ? `${NATIVE} Afterwards: ${facts.join(", ")}.` : NATIVE;
}

/** The `HandoverChecked` journal event in a line, or what it means that no handoff was kept. */
export function checkedNote(p: Record<string, unknown>): string {
  const num = (key: string) => (typeof p[key] === "number" ? (p[key] as number) : 0);
  if (p.source === "digest") {
    return (
      "No handoff from the agent was kept: a digest read from the journal stands in. A one-shot " +
      "transport (claude -p, codex exec) or a sandbox cannot be asked for one, and a reply without " +
      "Done and Remaining is dropped."
    );
  }
  const facts = [`${num("headings")} of 4 headings`];
  if (p.remaining_ok === false) facts.push("Remaining says little");
  if (p.done_names_path === false) facts.push("Done names no edited file");
  if (num("files_total")) facts.push(`${num("files_found")} of ${num("files_total")} edited files named`);
  if (num("failures_total")) facts.push(`${num("failures_found")} of ${num("failures_total")} failures named`);
  if (num("requests_total")) facts.push(`${num("requests_found")} of ${num("requests_total")} open requests named`);
  if (p.plan_step === false) facts.push("next plan step not named");
  if (num("unconfirmed")) facts.push(plural(num("unconfirmed"), "unconfirmed marker"));
  if (p.length_ok === false) facts.push("length unusual");
  return `Handoff: ${facts.join(", ")}. Checked by word match only; nothing was blocked.`;
}
