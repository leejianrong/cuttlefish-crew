// Unit: the goal box's rules (goal.ts).

import { describe, expect, it } from "vitest";
import { BUILDERS, builderLabel, canGive, defaultBuilder } from "./goal";

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
