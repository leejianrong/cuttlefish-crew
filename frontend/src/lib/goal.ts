// The goal box's rules (CUT-171): which builders can take a goal, the starting sentences, and what
// to call the one a project will use. Pure, so each is unit-tested.

export interface BuilderChoice {
  id: "claude-code" | "codex";
  label: string;
}

/** The tools that take a first message when they start. kopicode's REPL does not yet (CUT-178). */
export const BUILDERS: BuilderChoice[] = [
  { id: "claude-code", label: "Claude Code" },
  { id: "codex", label: "Codex" },
];

export const GOAL_EXAMPLES = [
  "Add a summary command and tests for it, then open a pull request.",
  "Find the most under-tested module and bring it up to a sensible level.",
  "Review the last week of commits for bugs and fix what you can.",
];

/** The builder a project starts on: its own backend when that can take a goal, else Claude Code. */
export function defaultBuilder(projectBackend: string | null | undefined): BuilderChoice["id"] {
  return BUILDERS.find((b) => b.id === projectBackend)?.id ?? "claude-code";
}

export function builderLabel(id: string | null | undefined): string {
  return BUILDERS.find((b) => b.id === id)?.label ?? "The builder";
}

/** What the page says about where a goal is being worked on, or null when it shares the checkout. */
export function branchLine(goal: { branch?: string } | null | undefined): string | null {
  return goal?.branch ? `on branch ${goal.branch}` : null;
}

/** Whether the box holds something to send. */
export function canGive(text: string): boolean {
  return text.trim().length > 0;
}
