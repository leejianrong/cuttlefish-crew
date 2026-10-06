// Pure helpers for the Permissions and Team tabs: drafts, comparisons, role edits. Nothing
// here touches the network or the DOM, so each rule is testable on its own.

import { FleetApiError, type AccessLevel, type BuiltinRole, type RoleDefinition } from "./api";

/** Whether two lists hold the same items, ignoring order. */
export function sameSet(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((item) => b.includes(item));
}

/** `enabled` with `name` switched on or off, kept in the catalogue's order. */
export function togglePreset(
  enabled: readonly string[],
  name: string,
  on: boolean,
  catalogue: readonly string[],
): string[] {
  const next = new Set(enabled);
  if (on) next.add(name);
  else next.delete(name);
  return catalogue.filter((entry) => next.has(entry));
}

/** The commands the operator declared on top of the presets, as one line each. */
export function allowToLines(allow: readonly (readonly string[])[]): string[] {
  return allow.map((command) => command.join(" "));
}

/** One typed command as an argv, or null if it is blank. */
export function parseCommand(text: string): string[] | null {
  const words = text.trim().split(/\s+/).filter(Boolean);
  return words.length > 0 ? words : null;
}

/** `lines` plus `line`, unless it is blank or already there. */
export function addCommand(lines: readonly string[], text: string): string[] {
  const words = parseCommand(text);
  if (!words) return [...lines];
  const line = words.join(" ");
  return lines.includes(line) ? [...lines] : [...lines, line];
}

export function removeCommand(lines: readonly string[], line: string): string[] {
  return lines.filter((entry) => entry !== line);
}

const ACCESS_NAMES: Record<string, string> = {
  "ask-first": "Ask first",
  standard: "Standard",
  auto: "Auto",
  "read-only": "Read-only",
};

/** A mode or access level as words: "ask-first" is "Ask first". */
export function modeLabel(mode: string): string {
  return ACCESS_NAMES[mode] ?? mode;
}

/** A role's access as words, falling back to the project's mode when it sets none. */
export function accessLabel(access: AccessLevel | null | undefined, projectMode: string): string {
  return access ? modeLabel(access) : `Project default (${modeLabel(projectMode)})`;
}

/** The agent behind a name from the daemon: "claude-code" is "Claude Code". */
export function backendLabel(name: string): string {
  const names: Record<string, string> = { "claude-code": "Claude Code", codex: "Codex" };
  return names[name] ?? name;
}

export function builtinByName(
  builtins: readonly BuiltinRole[],
  name: string,
): BuiltinRole | undefined {
  return builtins.find((entry) => entry.name === name);
}

/** A built-in as a role to register: its prompt as the persona, read-only where it is. */
export function roleFromBuiltin(builtin: BuiltinRole): RoleDefinition {
  return {
    name: builtin.name,
    persona: builtin.prompt,
    backend: null,
    access: builtin.access === "read-only" ? "read-only" : null,
  };
}

/** True while a built-in role still carries its built-in prompt. */
export function isDefaultPrompt(role: RoleDefinition, builtins: readonly BuiltinRole[]): boolean {
  const builtin = builtinByName(builtins, role.name);
  return builtin !== undefined && role.persona === builtin.prompt;
}

/** Whether "Reset to default" does anything: a built-in whose prompt or access was changed. */
export function canReset(role: RoleDefinition, builtins: readonly BuiltinRole[]): boolean {
  const builtin = builtinByName(builtins, role.name);
  if (!builtin) return false;
  const defaultAccess = builtin.access === "read-only" ? "read-only" : null;
  return role.persona !== builtin.prompt || (role.access ?? null) !== defaultAccess;
}

/** `role` with its built-in prompt and access restored; its backend is kept. */
export function resetRole(role: RoleDefinition, builtins: readonly BuiltinRole[]): RoleDefinition {
  const builtin = builtinByName(builtins, role.name);
  if (!builtin) return role;
  return { ...roleFromBuiltin(builtin), backend: role.backend ?? null };
}

/** Built-ins not yet on the team, for the "Add a role" menu. */
export function addableBuiltins(
  roles: readonly RoleDefinition[],
  builtins: readonly BuiltinRole[],
): BuiltinRole[] {
  const present = new Set(roles.map((role) => role.name));
  return builtins.filter((builtin) => !present.has(builtin.name));
}

export type RoleNameProblem = "empty" | "duplicate" | null;

export function roleNameProblem(name: string, roles: readonly RoleDefinition[]): RoleNameProblem {
  const trimmed = name.trim();
  if (!trimmed) return "empty";
  return roles.some((role) => role.name === trimmed) ? "duplicate" : null;
}

/** `roles` with the role named `name` replaced by `next` (same position). */
export function replaceRole(
  roles: readonly RoleDefinition[],
  name: string,
  next: RoleDefinition,
): RoleDefinition[] {
  return roles.map((role) => (role.name === name ? next : role));
}

export function removeRole(roles: readonly RoleDefinition[], name: string): RoleDefinition[] {
  return roles.filter((role) => role.name !== name);
}

/**
 * The command groups a new project should start with: the defaults, plus Go and Rust when the
 * folder looks like one, so a Go project can run `go test` without a visit to Permissions.
 * `null` means "just the defaults" (send nothing).
 */
export function presetsForLanguages(
  languages: readonly string[],
  defaults: readonly string[],
): string[] | null {
  const wantsGoRust = languages.some((language) => language === "Go" || language === "Rust");
  if (!wantsGoRust || defaults.includes("go-rust")) return null;
  return [...defaults, "go-rust"];
}

/** What to tell a person whose Save failed. The daemon refuses a bad command with a reason
 * (a 400 naming the command), and that reason is the useful part; anything else is most likely
 * the daemon being away. `applied` lists what an earlier step of the same Save did save. */
export function saveFailure(error: unknown, applied: readonly string[]): string {
  const saved = applied.length > 0 ? `Saved ${applied.join(" and ")}, but not the rest. ` : "";
  if (error instanceof FleetApiError && error.status === 400) {
    return `${saved}Couldn't save: ${error.message}.`;
  }
  return applied.length > 0
    ? `${saved}Check that the daemon is still running, then save again.`
    : "Couldn't save those changes. Check that the daemon is still running, then try again.";
}
