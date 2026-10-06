// Light, dark, or follow the OS. Remembered in this browser only, like the connection
// (session.ts): a per-viewer convenience that is safe to lose. "system" is the default and
// sets no attribute, so theme-tokens.css's own prefers-color-scheme rule decides.

export type ThemePreference = "system" | "light" | "dark";

const STORAGE_KEY = "cuttlefish.theme";
const ORDER: ThemePreference[] = ["system", "light", "dark"];

export function isThemePreference(value: unknown): value is ThemePreference {
  return value === "system" || value === "light" || value === "dark";
}

export function loadTheme(): ThemePreference {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return isThemePreference(raw) ? raw : "system";
  } catch {
    return "system";
  }
}

export function saveTheme(preference: ThemePreference): void {
  try {
    localStorage.setItem(STORAGE_KEY, preference);
  } catch {
    // Blocked site data: the choice just lasts for this page load.
  }
}

/** Set (or, for "system", remove) `data-theme` on the root element. */
export function applyTheme(
  preference: ThemePreference,
  root: Pick<HTMLElement, "setAttribute" | "removeAttribute"> = document.documentElement,
): void {
  if (preference === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", preference);
}

/** system -> light -> dark -> system. */
export function nextTheme(preference: ThemePreference): ThemePreference {
  return ORDER[(ORDER.indexOf(preference) + 1) % ORDER.length];
}
