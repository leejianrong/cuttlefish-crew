// Unit: the goal box's rules (goal.ts).

import { describe, expect, it } from "vitest";
import {
  BUILDERS,
  branchLine,
  builderLabel,
  canGive,
  canPushToFix,
  defaultBuilder,
  pullRequestNote,
} from "./goal";

describe("defaultBuilder", () => {
  it("keeps a project's own backend when it can take a goal", () => {
    expect(defaultBuilder("codex")).toBe("codex");
    expect(defaultBuilder("claude-code")).toBe("claude-code");
  });
  it("falls back to Claude Code for kopicode, which cannot take a first message, or nothing", () => {
    expect(defaultBuilder("kopicode")).toBe("claude-code");
    expect(defaultBuilder(null)).toBe("claude-code");
    expect(defaultBuilder(undefined)).toBe("claude-code");
  });
});

describe("builderLabel", () => {
  it("names a known builder and is neutral otherwise", () => {
    expect(builderLabel("codex")).toBe("Codex");
    expect(builderLabel("kopicode")).toBe("The builder");
    expect(builderLabel(null)).toBe("The builder");
    expect(BUILDERS.map((b) => b.id)).toEqual(["claude-code", "codex"]);
  });
});

describe("canGive", () => {
  it("needs something other than white space", () => {
    expect(canGive("  \n ")).toBe(false);
    expect(canGive(" do it ")).toBe(true);
  });
});

describe("branchLine", () => {
  it("names the branch of a goal worked on in its own worktree", () => {
    expect(branchLine({ branch: "cuttlefish/add-a-summary-1031-0905" })).toBe(
      "on branch cuttlefish/add-a-summary-1031-0905",
    );
  });
  it("says nothing for a goal in the project's own checkout", () => {
    expect(branchLine({})).toBeNull();
    expect(branchLine(null)).toBeNull();
  });
});

describe("pullRequestNote", () => {
  it("says a pull request was opened, or was already open", () => {
    expect(pullRequestNote({ ok: true, url: "u" })).toBe("Pull request opened.");
    expect(pullRequestNote({ ok: true, url: "u", existing: true })).toBe(
      "A pull request is already open.",
    );
  });
  it("gives the reason a refusal carries", () => {
    expect(pullRequestNote({ ok: false, reason: "gh is not logged in." })).toBe("gh is not logged in.");
  });
});

describe("canPushToFix", () => {
  it("is true only for an unpushed branch", () => {
    expect(canPushToFix({ ok: false, code: "not_pushed" })).toBe(true);
    expect(canPushToFix({ ok: false, code: "no_commits" })).toBe(false);
    expect(canPushToFix({ ok: true })).toBe(false);
  });
});
