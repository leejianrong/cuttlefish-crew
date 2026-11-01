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

/** The file an agent writes to have the dashboard walk through its artifacts in order (CUT-199). */
export const MANIFEST_PATH = "present.json";
const MAX_STEPS = 50;
const MAX_SAY = 1000;

export interface PresentStep {
  path: string;
  file: Artifact;
  say: string;
}

export interface Presentation {
  title: string;
  steps: PresentStep[];
  /** Items left out: a missing or unshowable file, or one that was not an object. */
  skipped: number;
}

/** The manifest's file, when the project has one. */
export function manifestFile(files: readonly Artifact[]): Artifact | null {
  return files.find((f) => f.path === MANIFEST_PATH && f.kind === "manifest") ?? null;
}

/** An agent's manifest as steps. It is the agent's file, so nothing in it is trusted: bad JSON or the
 * wrong shape is `null`, and an item that is malformed or names a file that is not there (or cannot
 * be shown) is skipped and counted, never thrown. The speaker text is shown as text, never as HTML. */
export function parsePresentation(text: string, files: readonly Artifact[]): Presentation | null {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null || Array.isArray(data)) return null;
  const { title, items } = data as { title?: unknown; items?: unknown };
  if (!Array.isArray(items)) return null;
  const steps: PresentStep[] = [];
  let skipped = 0;
  for (const item of items.slice(0, MAX_STEPS)) {
    const raw = typeof item === "object" && item !== null ? (item as Record<string, unknown>) : {};
    const path = typeof raw.path === "string" ? raw.path.replace(/^\.?\//, "") : "";
    const file = files.find((f) => f.path === path);
    if (!file || !isViewable(file.kind)) {
      skipped += 1;
      continue;
    }
    const say = typeof raw.say === "string" ? raw.say.slice(0, MAX_SAY) : "";
    steps.push({ path, file, say });
  }
  skipped += Math.max(0, items.length - MAX_STEPS);
  return {
    title: typeof title === "string" && title.trim() ? title.trim().slice(0, 120) : "Walk-through",
    steps,
    skipped,
  };
}
