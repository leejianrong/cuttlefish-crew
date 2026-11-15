import { describe, expect, it } from "vitest";
import { checkedNote, refreshNote } from "./handoff";

describe("refreshNote", () => {
  it("says no handoff was asked for and lists what followed", () => {
    const note = refreshNote({ repeated_commands: 2, repeated_edits: 1, plan_ticked: false, recovery_s: 240, nudges: 1 });
    expect(note).toContain("No handoff document was asked for");
    expect(note).toContain("2 repeated commands, 1 repeated edit, plan not touched since");
    expect(note).toContain("4 min later");
    expect(note).toContain("1 nudge.");
  });
  it("stays short when nothing is known", () => {
    expect(refreshNote(null)).toBe("No handoff document was asked for: Claude Code compacted its own context.");
  });
});

describe("checkedNote", () => {
  it("counts headings and named facts", () => {
    const note = checkedNote({
      source: "agent",
      headings: 3,
      remaining_ok: true,
      done_names_path: true,
      files_found: 2,
      files_total: 3,
      failures_total: 0,
      requests_total: 0,
      plan_step: false,
      unconfirmed: 1,
      length_ok: true,
    });
    expect(note).toBe(
      "Handoff: 3 of 4 headings, 2 of 3 edited files named, next plan step not named, 1 unconfirmed marker. " +
        "Checked by word match only; nothing was blocked.",
    );
  });
  it("says a digest stands in and that one-shot transports cannot be asked", () => {
    const note = checkedNote({ source: "digest" });
    expect(note).toContain("digest");
    expect(note).toContain("claude -p");
  });
});
