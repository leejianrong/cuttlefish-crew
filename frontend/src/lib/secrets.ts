// Words and checks for the Secrets tab. The daemon sends names and kinds, never values; what a
// person reads about them is decided here.

import { FleetApiError, type ProjectSummary } from "./api";

/** The name rule the daemon enforces (a secret becomes an environment variable), checked here
 * first so a typo is caught before a round trip. null when the name is fine. */
export function secretNameProblem(name: string): string | null {
  if (name.length === 0) return "Enter a name, like GITHUB_TOKEN.";
  if (name.length > 128) return "A name is at most 128 characters.";
  if (!/^[A-Z_][A-Z0-9_]*$/.test(name)) {
    return "Use capital letters, digits and underscores, starting with a letter, like GITHUB_TOKEN.";
  }
  return null;
}

export const MAX_SECRET_VALUE = 16384;

export function secretValueProblem(value: string): string | null {
  if (value.length === 0) return "Enter a value. It is encrypted on save and cannot be shown again.";
  if (value.length > MAX_SECRET_VALUE) return `A value is at most ${MAX_SECRET_VALUE} characters.`;
  return null;
}

/** The variable each backend runs on, so the person is offered the name it reads. */
const CREDENTIAL_OF: Record<string, string> = {
  kopicode: "OPENROUTER_API_KEY",
  "claude-code": "ANTHROPIC_API_KEY",
  codex: "OPENAI_API_KEY",
};

/** Credential names worth suggesting for this project: what its backends read, minus what is
 * already set (its own or shared). A project that names no backend runs the daemon's default,
 * which is kopicode unless the daemon says otherwise. */
export function suggestedNames(project: ProjectSummary, alreadySet: readonly string[]): string[] {
  const backends = new Set<string>();
  if (project.backend) backends.add(project.backend);
  for (const role of project.roles) if (role.backend) backends.add(role.backend);
  if (backends.size === 0) backends.add("kopicode");
  return [...backends]
    .map((backend) => CREDENTIAL_OF[backend])
    .filter((name): name is string => name !== undefined && !alreadySet.includes(name));
}

/** What an agent credential's row says about who can see it, per what was checked
 * (docs/research/harness-credentials-spike.md). */
export function credentialNote(name: string, brokered = false): string {
  switch (name) {
    case "OPENROUTER_API_KEY":
      return "Powers kopicode. Kept out of the commands it runs.";
    case "OPENAI_API_KEY":
      return brokered
        ? "Powers Codex. Held by cuttlefish: its commands see only a short-lived token."
        : "Powers Codex. Kept out of the commands it runs.";
    case "ANTHROPIC_API_KEY":
      return brokered
        ? "Powers Claude Code. Held by cuttlefish: its commands see only a short-lived token."
        : "Powers Claude Code. Its commands can see this key.";
    default:
      return "Powers an agent.";
  }
}

/** A failed save or remove, in words that say what to do. */
export function secretFailure(error: unknown): string {
  if (error instanceof FleetApiError) {
    if (error.status === 400 || error.status === 409) return error.message;
    if (error.status === 404) return "That secret is no longer there. Reload the tab.";
  }
  return "Couldn't reach the daemon. Check that it is still running.";
}

/** Every variable a backend runs on, for the shared screen's suggestions. */
export const CREDENTIAL_NAMES: readonly string[] = Object.values(CREDENTIAL_OF);

/** `a, b and c` / `a and b` / `a`. */
export function listNames(names: readonly string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** What the Remove dialog says removing a shared secret does. */
export function sharedRemovalNote(lostBy: readonly string[]): string {
  const cannotUndo = "This cannot be undone, and the value cannot be shown again.";
  if (lostBy.length === 0) {
    return `No project is using it without a value of its own. ${cannotUndo}`;
  }
  const count = lostBy.length === 1 ? "1 project loses it" : `${lostBy.length} projects lose it`;
  return `${count} the next time its team starts: ${listNames(lostBy)}. Projects with their own value of this name keep it. ${cannotUndo}`;
}

/** The tag on a shared secret that some projects override. */
export function overrideTag(overriddenIn: readonly string[]): string | null {
  if (overriddenIn.length === 0) return null;
  const where = overriddenIn.length === 1 ? "1 project" : `${overriddenIn.length} projects`;
  return `Overridden in ${where}: ${listNames(overriddenIn)}`;
}
