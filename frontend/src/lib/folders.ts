// Pure helpers for the add-project screen: nothing here touches the network or the DOM.

export interface Crumb {
  label: string;
  path: string;
}

/** The last segment of a path, ignoring trailing slashes: "/a/b/" -> "b". */
export function folderName(path: string): string {
  const segments = path.split("/").filter(Boolean);
  return segments.length > 0 ? segments[segments.length - 1] : path;
}

/**
 * Breadcrumbs from the browse root down to `path`. The root's own crumb is labelled "~" when
 * it is the user's home (so `/home/jian` reads as "~"), else by its folder name.
 */
export function breadcrumbs(path: string, root: string, home?: string): Crumb[] {
  const rootLabel = home !== undefined && root === home ? "~" : folderName(root);
  const crumbs: Crumb[] = [{ label: rootLabel, path: root }];
  if (path === root || !path.startsWith(root.endsWith("/") ? root : `${root}/`)) return crumbs;
  const rest = path.slice(root.length).split("/").filter(Boolean);
  let current = root.endsWith("/") ? root.slice(0, -1) : root;
  for (const segment of rest) {
    current = `${current}/${segment}`;
    crumbs.push({ label: segment, path: current });
  }
  return crumbs;
}

/**
 * One shell command per line, split on whitespace. Unlike the CLI's `--allow` this does not
 * shell-quote, a deliberate simplification (ADR-0009, Q53).
 */
export function parseAllow(text: string): string[][] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.split(/\s+/));
}

/** A positive number from a text field, or null for blank. NaN and negatives are errors. */
export function parseLimit(text: string): number | null | "invalid" {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) && value >= 0 ? value : "invalid";
}
