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

/** The policy an agent's HTML runs under (CUT-175, ADR-0044): nothing may be fetched, framed,
 * submitted or navigated to, and nothing outside the document itself is loaded. Inline style and
 * script and `data:` or `blob:` images, media and fonts work, so a self-contained design does. */
export const FRAME_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  "img-src data: blob:",
  "media-src data: blob:",
  "font-src data:",
  "form-action 'none'",
  "base-uri 'none'",
  "frame-src 'none'",
].join("; ");

/** The `sandbox` attribute: scripts, and nothing else. Without `allow-same-origin` the frame has an
 * opaque origin, so it can read neither the dashboard's storage nor its token; without
 * `allow-top-navigation`, `allow-forms`, `allow-popups` or `allow-downloads` it can leave nowhere. */
export const FRAME_SANDBOX = "allow-scripts";

/** An agent's HTML as the frame's `srcdoc`: the policy first, so nothing in the file can come before
 * it, then the file. The policy is also set by the sandbox's own limits, not only by this tag. */
export function htmlFrameDoc(html: string): string {
  return `<!doctype html><meta http-equiv="Content-Security-Policy" content="${FRAME_CSP}">${html}`;
}

/** Whether the dashboard can show this kind of file (the rest are listed by name). */
export function isViewable(kind: string): boolean {
  return kind === "markdown" || kind === "image" || kind === "video" || kind === "html";
}

/** The files worth a place on the canvas, newest first, at most `limit`. */
export function canvasFiles(files: readonly Artifact[], limit = 8): Artifact[] {
  return files.filter((f) => isViewable(f.kind) && f.path !== "plan.md").slice(0, limit);
}

export function refusalText(status: number): string {
  if (status === 413) return "This file is too large to show.";
  if (status === 415) return "This kind of file is listed but not shown.";
  if (status === 404) return "This file is gone.";
  return "Couldn't read this file.";
}
