// Unit: the Environment card's plain-language rows (environment.ts).

import { describe, expect, it } from "vitest";
import type { EcosystemEnv } from "./api";
import type { EpisodicEventView, PrepareInfo } from "./api";
import {
  PREPARE_SETTINGS,
  environmentRow,
  environmentRows,
  hasMissingInstall,
  installProgress,
  missingInstallNote,
  stepLines,
} from "./environment";

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
      prepare: { setting: "ask", steps: [], unsupported: [] },
      ecosystems: [env({ installed: true, env_dir: ".venv" })],
    });
    const missing = environmentRows({
      root: "/r",
      root_exists: true,
      prepare: { setting: "ask", steps: [], unsupported: [] },
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

describe("the install settings", () => {
  it("offers ask, automatically and never, each with a plain sentence", () => {
    expect(PREPARE_SETTINGS.map((entry) => entry.id)).toEqual(["ask", "auto", "off"]);
    expect(PREPARE_SETTINGS.every((entry) => entry.text.length > 20)).toBe(true);
  });
  it("says what happens to a missing install under each setting", () => {
    expect(missingInstallNote("ask")).toContain("ask before installing");
    expect(missingInstallNote("auto")).toContain("will install");
    expect(missingInstallNote("off")).toContain("tests that need it will fail");
  });
});

describe("stepLines", () => {
  it("shows exactly the commands that would run, one per line", () => {
    const prepare: PrepareInfo = {
      setting: "ask",
      unsupported: [],
      steps: [
        {
          ecosystem: "python",
          name: "Python",
          commands: [
            ["uv", "venv"],
            ["uv", "pip", "install", "-r", "requirements.txt"],
          ],
          reason: ".venv is missing",
        },
      ],
    };
    expect(stepLines(prepare)).toEqual([
      {
        name: "Python",
        commands: ["uv venv", "uv pip install -r requirements.txt"],
        reason: ".venv is missing",
      },
    ]);
  });
});

describe("installProgress", () => {
  const at = (seq: number, type: string, payload: Record<string, unknown>) =>
    ({ seq, ts: "2026-10-07T10:00:00Z", event_type: type, payload }) as EpisodicEventView;
  const started = at(1, "EnvironmentPrepareStarted", {
    ecosystem: "node",
    commands: [["npm", "ci"]],
    reason: "node_modules is missing",
  });

  it("reports an install that has started and not finished", () => {
    expect(installProgress([started])).toEqual({
      name: "Node",
      commands: "npm ci",
      reason: "node_modules is missing",
    });
  });
  it("is null once it is done, or when nothing was installed", () => {
    const done = at(2, "EnvironmentPrepared", { ecosystem: "node", ok: true });
    expect(installProgress([started, done])).toBeNull();
    expect(installProgress([])).toBeNull();
  });
  it("follows the one still running when several ecosystems install in turn", () => {
    const python = at(1, "EnvironmentPrepareStarted", {
      ecosystem: "python",
      commands: [["uv", "sync"]],
      reason: ".venv is missing",
    });
    const pythonDone = at(2, "EnvironmentPrepared", { ecosystem: "python", ok: true });
    const node = { ...started, seq: 3 };
    expect(installProgress([python, pythonDone, node])?.name).toBe("Node");
  });
});
