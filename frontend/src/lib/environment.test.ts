// Unit: the Environment card's plain-language rows (environment.ts).

import { describe, expect, it } from "vitest";
import type { EcosystemEnv } from "./api";
import { environmentRow, environmentRows, hasMissingInstall } from "./environment";

function env(over: Partial<EcosystemEnv>): EcosystemEnv {
  return {
    ecosystem: "python",
    tool: null,
    manifests: [],
    lockfile: null,
    version_hint: null,
    env_dir: null,
    installed: null,
    notes: [],
    ...over,
  };
}

describe("environmentRow", () => {
  it("names the tool, the version asked for and the lockfile", () => {
    const row = environmentRow(
      env({
        tool: "uv",
        version_hint: ">=3.12",
        lockfile: "uv.lock",
        installed: true,
        env_dir: ".venv",
      }),
    );
    expect(row.name).toBe("Python");
    expect(row.details).toEqual(["uv", "wants >=3.12", "uv.lock"]);
    expect(row).toMatchObject({ state: "installed", stateLabel: ".venv is there" });
  });
  it("says what is missing in the ecosystem's own folder", () => {
    expect(environmentRow(env({ installed: false })).stateLabel).toBe(".venv is missing");
    expect(
      environmentRow(env({ ecosystem: "node", tool: "pnpm", installed: false })).stateLabel,
    ).toBe("node_modules is missing");
  });
  it("shows no state where there is nothing in the folder to look for", () => {
    const row = environmentRow(
      env({ ecosystem: "go", tool: "go", notes: ["modules are cached outside the project"] }),
    );
    expect(row.state).toBeNull();
    expect(row.stateLabel).toBeNull();
    expect(row.details).toContain("modules are cached outside the project");
  });
});

describe("environmentRows and hasMissingInstall", () => {
  it("warns only when some install is missing", () => {
    const ok = environmentRows({
      root: "/r",
      root_exists: true,
      ecosystems: [env({ installed: true, env_dir: ".venv" })],
    });
    const missing = environmentRows({
      root: "/r",
      root_exists: true,
      ecosystems: [
        env({ installed: true, env_dir: ".venv" }),
        env({ ecosystem: "node", installed: false }),
      ],
    });
    expect(hasMissingInstall(ok)).toBe(false);
    expect(hasMissingInstall(missing)).toBe(true);
    expect(hasMissingInstall([])).toBe(false);
  });
});
