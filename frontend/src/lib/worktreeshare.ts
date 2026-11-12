// What a goal's worktree shares with the checkout (CUT-296 API, CUT-305 screen).

/** One path per line (commas also split), trimmed, blanks and repeats dropped. */
export function parsePaths(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(/[\n,]/)) {
    const path = part.trim();
    if (path) seen.add(path);
  }
  return [...seen];
}

export interface ShareReport {
  linked?: string[];
  copied?: string[];
  skipped?: { path: string; why: string }[];
}

/** What the worktree got, in one line each: what was linked, copied and left out and why. */
export function shareNotes(report: ShareReport | undefined): string[] {
  if (!report) return [];
  const notes: string[] = [];
  if (report.linked?.length) notes.push(`Linked: ${report.linked.join(", ")}`);
  if (report.copied?.length) notes.push(`Copied: ${report.copied.join(", ")}`);
  for (const s of report.skipped ?? []) notes.push(`Left out ${s.path}: ${s.why}`);
  return notes;
}
