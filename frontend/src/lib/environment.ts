// Plain-language lines for the project's Environment card (V5-E2). The daemon reads the project's
// files and says what it found; nothing here, or there, runs anything.

import type {
  EcosystemEnv,
  EnvironmentSpec,
  EpisodicEventView,
  PrepareInfo,
  PrepareSetting,
} from "./api";

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

export const PREPARE_SETTINGS: { id: PrepareSetting; label: string; text: string }[] = [
  {
    id: "ask",
    label: "Ask me",
    text: "When something needs installing, cuttlefish asks before it runs the project's install scripts.",
  },
  {
    id: "auto",
    label: "Automatically",
    text: "cuttlefish installs what is missing or out of date when a team starts, without asking.",
  },
  {
    id: "off",
    label: "Never",
    text: "cuttlefish never installs. Agents start without the dependencies, and tests that need them will fail.",
  },
];

/** What the card says about missing installs, by the project's setting. */
export function missingInstallNote(setting: PrepareSetting): string {
  return {
    ask: "When you start a team, cuttlefish will ask before installing what is missing.",
    auto: "cuttlefish will install what is missing when you start a team.",
    off: "cuttlefish will not install it, so agents start without it and tests that need it will fail.",
  }[setting];
}

export interface StepLine {
  name: string;
  /** Exactly what runs, one command per line (a step can be several). */
  commands: string[];
  reason: string;
}

export function stepLines(prepare: PrepareInfo): StepLine[] {
  return prepare.steps.map((step) => ({
    name: step.name,
    commands: step.commands.map((command) => command.join(" ")),
    reason: step.reason,
  }));
}

export interface InstallProgress {
  name: string;
  commands: string;
  reason: string;
}

/** The install that is running right now: the latest "started" with no "done" after it for the
 * same ecosystem. Null when nothing is installing. */
export function installProgress(events: readonly EpisodicEventView[]): InstallProgress | null {
  const open = new Map<string, InstallProgress>();
  for (const event of events) {
    const ecosystem = String(event.payload.ecosystem ?? "");
    if (event.event_type === "EnvironmentPrepareStarted") {
      open.set(ecosystem, {
        name: NAMES[ecosystem as EcosystemEnv["ecosystem"]] ?? ecosystem,
        commands: (event.payload.commands as string[][]).map((c) => c.join(" ")).join(" && "),
        reason: String(event.payload.reason),
      });
    } else if (event.event_type === "EnvironmentPrepared") {
      open.delete(ecosystem);
    }
  }
  const last = [...open.values()].at(-1);
  return last ?? null;
}
