// Unit: the goal box's rules (goal.ts).

import { describe, expect, it } from "vitest";
import { BUILDERS, branchLine, builderLabel, canGive, defaultBuilder } from "./goal";

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
