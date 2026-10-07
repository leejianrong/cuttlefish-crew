// Plain-language lines for the project's Environment card (V5-E2). The daemon reads the project's
// files and says what it found; nothing here, or there, runs anything.

import type { EcosystemEnv, EnvironmentSpec } from "./api";

const NAMES: Record<EcosystemEnv["ecosystem"], string> = {
  python: "Python",
  node: "Node",
  go: "Go",
  rust: "Rust",
  java: "Java",
  ruby: "Ruby",
};

export interface EnvironmentRow {
  name: string;
  /** Tool, version and lockfile, as short phrases for one line. */
  details: string[];
  /** What a person can act on: the project's own install is there or missing. Absent when
   * there is nothing in the project folder to look for. */
  state: "installed" | "missing" | null;
  stateLabel: string | null;
}

export function environmentRow(env: EcosystemEnv): EnvironmentRow {
  const details: string[] = [];
  if (env.tool) details.push(env.tool);
  if (env.version_hint) details.push(`wants ${env.version_hint}`);
  if (env.lockfile) details.push(env.lockfile);
  details.push(...env.notes);
  const folder = env.ecosystem === "python" ? ".venv" : "node_modules";
  const state = env.installed === null ? null : env.installed ? "installed" : "missing";
  return {
    name: NAMES[env.ecosystem],
    details,
    state,
    stateLabel:
      state === null
        ? null
        : state === "installed"
          ? `${env.env_dir ?? folder} is there`
          : `${folder} is missing`,
  };
}

export function environmentRows(spec: EnvironmentSpec): EnvironmentRow[] {
  return spec.ecosystems.map(environmentRow);
}

/** Whether to warn that agents will run without the project's dependencies. cuttlefish does not
 * install them yet (V5-E3), so a missing install means tests that need it will fail. */
export function hasMissingInstall(rows: readonly EnvironmentRow[]): boolean {
  return rows.some((row) => row.state === "missing");
}
