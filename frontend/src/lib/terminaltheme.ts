// The terminal's own light, dark or follow-the-app choice (CUT-169). Remembered in this browser only,
// like the app theme (theme.ts): a per-viewer convenience that is safe to lose. The terminal is dark
// by default, in both app themes, which is why "dark" and not "match" is the default.

export type TerminalThemePreference = "match" | "dark" | "light";
export type TerminalMode = "dark" | "light";

const STORAGE_KEY = "cuttlefish.terminal-theme";

export const TERMINAL_THEME_CHOICES: { id: TerminalThemePreference; label: string }[] = [
  { id: "match", label: "Match app" },
  { id: "dark", label: "Dark" },
  { id: "light", label: "Light" },
];

export function isTerminalThemePreference(value: unknown): value is TerminalThemePreference {
  return value === "match" || value === "dark" || value === "light";
}

export function loadTerminalTheme(): TerminalThemePreference {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return isTerminalThemePreference(raw) ? raw : "dark";
  } catch {
    return "dark";
  }
}

export function saveTerminalTheme(preference: TerminalThemePreference): void {
  try {
    localStorage.setItem(STORAGE_KEY, preference);
  } catch {
    // Blocked site data: the choice just lasts for this page load.
  }
}

/** Which palette to paint. `appDark` is whether the app itself is wearing its dark theme. */
export function resolveTerminalMode(
  preference: TerminalThemePreference,
  appDark: boolean,
): TerminalMode {
  if (preference === "match") return appDark ? "dark" : "light";
  return preference;
}

/** Whether the app is dark now: a forced `data-theme`, else the OS setting. */
export function appIsDark(
  root: Pick<HTMLElement, "getAttribute"> = document.documentElement,
  prefersDark: () => boolean = () => window.matchMedia("(prefers-color-scheme: dark)").matches,
): boolean {
  const forced = root.getAttribute("data-theme");
  if (forced === "dark") return true;
  if (forced === "light") return false;
  return prefersDark();
}

/** The custom property names for one palette, in xterm's order. */
export function ansiNames(mode: TerminalMode): string[] {
  return Array.from({ length: 16 }, (_, i) => `--term-${mode}-ansi-${i}`);
}
