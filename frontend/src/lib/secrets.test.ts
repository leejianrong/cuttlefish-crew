// Unit: the Secrets tab's name and value checks and wording (secrets.ts).

import { describe, expect, it } from "vitest";
import { FleetApiError, type ProjectSummary } from "./api";
import {
  credentialNote,
  listNames,
  overrideTag,
  secretFailure,
  secretNameProblem,
  secretValueProblem,
  sharedRemovalNote,
  suggestedNames,
} from "./secrets";

function project(over: Partial<ProjectSummary> = {}): ProjectSummary {
  return { backend: null, roles: [], ...over } as unknown as ProjectSummary;
}

describe("secretNameProblem", () => {
  it("accepts a name that can be an environment variable", () => {
    for (const name of ["GITHUB_TOKEN", "_X", "A1_B2"]) expect(secretNameProblem(name)).toBeNull();
  });
  it("says what is wrong, in words", () => {
    expect(secretNameProblem("")).toContain("Enter a name");
    expect(secretNameProblem("github_token")).toContain("capital letters");
    expect(secretNameProblem("1TOKEN")).toContain("capital letters");
    expect(secretNameProblem("MY-TOKEN")).toContain("capital letters");
    expect(secretNameProblem("X".repeat(129))).toContain("128");
  });
});

describe("secretValueProblem", () => {
  it("needs a value, and not a huge one", () => {
    expect(secretValueProblem("")).toContain("Enter a value");
    expect(secretValueProblem("x".repeat(16385))).toContain("16384");
    expect(secretValueProblem("x")).toBeNull();
  });
});

describe("suggestedNames", () => {
  it("offers the key of the backend the project runs, kopicode by default", () => {
    expect(suggestedNames(project(), [])).toEqual(["OPENROUTER_API_KEY"]);
  });
  it("offers each backend's key, once, and skips what is already set", () => {
    const mixed = project({
      backend: "claude-code",
      roles: [{ name: "r", backend: "codex" }, { name: "s", backend: "codex" }] as never,
    });
    expect(suggestedNames(mixed, [])).toEqual(["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]);
    expect(suggestedNames(mixed, ["OPENAI_API_KEY"])).toEqual(["ANTHROPIC_API_KEY"]);
  });
});

describe("credentialNote", () => {
  it("says what was checked about who can see each key", () => {
    expect(credentialNote("OPENROUTER_API_KEY")).toContain("Kept out");
    expect(credentialNote("OPENAI_API_KEY")).toContain("Kept out");
    expect(credentialNote("ANTHROPIC_API_KEY")).toContain("can see this key");
  });

  it("says a held key is held, not kept out, when the broker is on", () => {
    expect(credentialNote("ANTHROPIC_API_KEY", true)).toContain("Held by cuttlefish");
    expect(credentialNote("ANTHROPIC_API_KEY", true)).not.toContain("can see this key");
    expect(credentialNote("OPENAI_API_KEY", true)).toContain("Held by cuttlefish");
    expect(credentialNote("OPENROUTER_API_KEY", true)).toContain("Kept out");
  });
});

describe("secretFailure", () => {
  it("passes the daemon's own words for a bad value and for secrets being off", () => {
    expect(secretFailure(new FleetApiError(400, "a secret needs a value"))).toBe("a secret needs a value");
    expect(secretFailure(new FleetApiError(409, "Secrets are off"))).toBe("Secrets are off");
  });
  it("says what to do when it is gone or unreachable", () => {
    expect(secretFailure(new FleetApiError(404, "x"))).toContain("no longer there");
    expect(secretFailure(new Error("network"))).toContain("Couldn't reach");
  });
});

describe("shared secret wording", () => {
  it("lists names with commas and an and", () => {
    expect(listNames([])).toBe("");
    expect(listNames(["a"])).toBe("a");
    expect(listNames(["a", "b"])).toBe("a and b");
    expect(listNames(["a", "b", "c"])).toBe("a, b and c");
  });
  it("says how many projects lose a removed shared secret, and which", () => {
    expect(sharedRemovalNote(["alpha"])).toContain("1 project loses it");
    const many = sharedRemovalNote(["alpha", "beta", "gamma"]);
    expect(many).toContain("3 projects lose it");
    expect(many).toContain("alpha, beta and gamma");
    expect(many).toContain("keep it");
    expect(sharedRemovalNote([])).toContain("No project is using it");
  });
  it("tags a shared secret that projects override", () => {
    expect(overrideTag([])).toBeNull();
    expect(overrideTag(["demo-app"])).toBe("Overridden in 1 project: demo-app");
    expect(overrideTag(["a", "b"])).toBe("Overridden in 2 projects: a and b");
  });
});
