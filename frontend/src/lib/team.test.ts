// Unit: the Permissions and Team tabs' pure helpers (team.ts).

import { describe, expect, it } from "vitest";
import type { BuiltinRole, RoleDefinition } from "./api";
import {
  accessLabel,
  backendLabel,
  modeLabel,
  addCommand,
  addableBuiltins,
  allowToLines,
  canReset,
  isDefaultPrompt,
  parseCommand,
  presetsForLanguages,
  removeCommand,
  removeRole,
  replaceRole,
  resetRole,
  roleFromBuiltin,
  roleNameProblem,
  sameSet,
  togglePreset,
} from "./team";

const builtins: BuiltinRole[] = [
  { name: "builder", summary: "b", prompt: "Build it.", access: "standard" },
  { name: "reviewer", summary: "r", prompt: "Review it.", access: "read-only" },
  { name: "tester", summary: "t", prompt: "Test it.", access: "standard" },
];

describe("sameSet", () => {
  it("ignores order and compares membership", () => {
    expect(sameSet(["a", "b"], ["b", "a"])).toBe(true);
    expect(sameSet(["a"], ["a", "b"])).toBe(false);
    expect(sameSet(["a", "b"], ["a", "c"])).toBe(false);
  });
});

describe("togglePreset", () => {
  const catalogue = ["inspect", "git-read", "python", "containers"];
  it("switches one on and keeps catalogue order", () => {
    expect(togglePreset(["python", "inspect"], "containers", true, catalogue)).toEqual([
      "inspect",
      "python",
      "containers",
    ]);
  });
  it("switches one off", () => {
    expect(togglePreset(["inspect", "python"], "python", false, catalogue)).toEqual(["inspect"]);
  });
  it("is idempotent", () => {
    expect(togglePreset(["inspect"], "inspect", true, catalogue)).toEqual(["inspect"]);
  });
});

describe("extra commands", () => {
  it("parses a typed command and rejects blank", () => {
    expect(parseCommand("  go   test ./... ")).toEqual(["go", "test", "./..."]);
    expect(parseCommand("   ")).toBeNull();
  });
  it("adds without blanks or duplicates", () => {
    expect(addCommand(["go test"], "  ")).toEqual(["go test"]);
    expect(addCommand(["go test"], "go  test")).toEqual(["go test"]);
    expect(addCommand(["go test"], "make lint")).toEqual(["go test", "make lint"]);
  });
  it("removes one and turns argv lists into lines", () => {
    expect(removeCommand(["a", "b"], "a")).toEqual(["b"]);
    expect(allowToLines([["go", "test"], ["make", "ci"]])).toEqual(["go test", "make ci"]);
  });
});

describe("accessLabel", () => {
  it("names an explicit access", () => {
    expect(accessLabel("read-only", "auto")).toBe("Read-only");
    expect(accessLabel("standard", "auto")).toBe("Standard");
  });
  it("falls back to the project's mode", () => {
    expect(accessLabel(null, "auto")).toBe("Project default (Auto)");
    expect(accessLabel(undefined, "ask-first")).toBe("Project default (Ask first)");
  });
});

describe("built-in roles", () => {
  it("registers a built-in with its prompt and read-only where it is", () => {
    expect(roleFromBuiltin(builtins[1])).toEqual({
      name: "reviewer",
      persona: "Review it.",
      backend: null,
      access: "read-only",
    });
    expect(roleFromBuiltin(builtins[0]).access).toBeNull();
  });

  it("is default only while the prompt is the built-in one", () => {
    expect(isDefaultPrompt(roleFromBuiltin(builtins[0]), builtins)).toBe(true);
    expect(isDefaultPrompt({ name: "builder", persona: "x" }, builtins)).toBe(false);
    expect(isDefaultPrompt({ name: "poet", persona: "Build it." }, builtins)).toBe(false);
  });

  it("can reset an edited built-in, and only that", () => {
    const edited: RoleDefinition = { name: "reviewer", persona: "Review it.", access: null };
    expect(canReset(roleFromBuiltin(builtins[1]), builtins)).toBe(false);
    expect(canReset(edited, builtins)).toBe(true); // access was changed
    expect(canReset({ name: "builder", persona: "x" }, builtins)).toBe(true);
    expect(canReset({ name: "poet", persona: "x" }, builtins)).toBe(false);
  });

  it("resets prompt and access but keeps the backend", () => {
    const edited: RoleDefinition = { name: "reviewer", persona: "x", backend: "codex", access: null };
    expect(resetRole(edited, builtins)).toEqual({
      name: "reviewer",
      persona: "Review it.",
      backend: "codex",
      access: "read-only",
    });
    const custom: RoleDefinition = { name: "poet", persona: "rhyme" };
    expect(resetRole(custom, builtins)).toBe(custom);
  });

  it("offers only built-ins the team does not have yet", () => {
    const team = [roleFromBuiltin(builtins[0])];
    expect(addableBuiltins(team, builtins).map((b) => b.name)).toEqual(["reviewer", "tester"]);
  });
});

describe("role list edits", () => {
  const team: RoleDefinition[] = [
    { name: "builder", persona: "a" },
    { name: "reviewer", persona: "b" },
  ];
  it("replaces in place and removes by name", () => {
    expect(replaceRole(team, "builder", { name: "builder", persona: "z" })[0].persona).toBe("z");
    expect(replaceRole(team, "builder", { name: "builder", persona: "z" })).toHaveLength(2);
    expect(removeRole(team, "builder").map((r) => r.name)).toEqual(["reviewer"]);
  });
  it("flags an empty or duplicate name", () => {
    expect(roleNameProblem("  ", team)).toBe("empty");
    expect(roleNameProblem("builder", team)).toBe("duplicate");
    expect(roleNameProblem(" poet ", team)).toBeNull();
  });
});

describe("presetsForLanguages", () => {
  const defaults = ["inspect", "git-read", "python"];
  it("adds Go and Rust for a Go or Rust folder", () => {
    expect(presetsForLanguages(["Go"], defaults)).toEqual([...defaults, "go-rust"]);
    expect(presetsForLanguages(["Python", "Rust"], defaults)).toEqual([...defaults, "go-rust"]);
  });
  it("sends nothing when the defaults already fit", () => {
    expect(presetsForLanguages(["Python"], defaults)).toBeNull();
    expect(presetsForLanguages([], defaults)).toBeNull();
    expect(presetsForLanguages(["Go"], [...defaults, "go-rust"])).toBeNull();
  });
});

describe("labels", () => {
  it("names modes and backends in words", () => {
    expect(modeLabel("ask-first")).toBe("Ask first");
    expect(modeLabel("auto")).toBe("Auto");
    expect(backendLabel("claude-code")).toBe("Claude Code");
    expect(backendLabel("kopicode")).toBe("kopicode");
  });
});
