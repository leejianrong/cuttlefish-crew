// Small helpers for the Artifacts tab (CUT-97).
import type { Artifact } from "./api";

/** "3 min ago" for a file's modified time (seconds since the epoch). */
export function ago(modified: number, now = Date.now() / 1000): string {
  const seconds = Math.max(0, Math.round(now - modified));
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

/** `ago` for an ISO 8601 timestamp, as the daemon sends one. Empty when it is missing or unreadable. */
export function agoIso(iso: string | null | undefined, now = Date.now() / 1000): string {
  const ms = iso ? Date.parse(iso) : Number.NaN;
  return Number.isNaN(ms) ? "" : ago(ms / 1000, now);
}

export function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The same list, so a poll that finds nothing new does not re-render the page. */
export function sameArtifacts(a: Artifact[], b: Artifact[]): boolean {
  return (
    a.length === b.length &&
    a.every((x, i) => x.path === b[i].path && x.modified === b[i].modified && x.size === b[i].size)
  );
}

/** What to show when the folder is empty or a file cannot be shown. */
export const EMPTY_TEXT =
  "Nothing here yet. An agent shows a result by writing a markdown file to .cuttlefish/artifacts/ in the project.";

export function refusalText(status: number): string {
  if (status === 413) return "This file is too large to show.";
  if (status === 415) return "This kind of file is listed but not shown.";
  if (status === 404) return "This file is gone.";
  return "Couldn't read this file.";
}
