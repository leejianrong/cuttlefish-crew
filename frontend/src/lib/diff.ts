// What the diff card says about a goal branch's changes (CUT-289). Pure, so each is unit-tested.
import type { DiffFile, GoalDiffResult } from "./api";

/** "3 files, +12 -4" for the card's header. */
export function diffSummary(diff: Pick<GoalDiffResult, "files" | "additions" | "deletions">): string {
  const n = diff.files?.length ?? 0;
  if (n === 0) return "No changes yet";
  return `${n} file${n === 1 ? "" : "s"}, +${diff.additions ?? 0} −${diff.deletions ?? 0}`;
}

const STATUS_WORDS: Record<string, string> = {
  added: "added",
  deleted: "deleted",
  renamed: "renamed",
  untracked: "new, not committed",
};

/** The status word beside a file, or null for a plain edit. */
export function statusLabel(file: Pick<DiffFile, "status">): string | null {
  return STATUS_WORDS[file.status] ?? null;
}

/** Why a file has no lines to show, or null when it has some. */
export function noLinesNote(file: DiffFile): string | null {
  if (file.withheld) return "Contents not shown: this file looks like a secret or is ignored by git.";
  if (file.binary) return "Binary file, not shown.";
  if (file.omitted) return "Too large to show with the rest of this diff.";
  if (file.hunks.length === 0) return file.status === "renamed" ? "Renamed with no changes." : "No line changes.";
  return null;
}

/** What the card says about where the changes come from. */
export function sourceNote(diff: Pick<GoalDiffResult, "mode" | "dirty" | "base">): string {
  const from = `against ${diff.base ?? "the default branch"}`;
  if (diff.mode === "branch") return `The branch's commits ${from}. Its worktree has been removed.`;
  return diff.dirty ? `Commits and uncommitted work ${from}.` : `Commits ${from}.`;
}

/** The first column of a diff row: the number it has on the side(s) that exist. */
export function lineNumber(line: { old?: number; new?: number }): string {
  return String(line.new ?? line.old ?? "");
}
