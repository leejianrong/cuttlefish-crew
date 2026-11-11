// What each agent backend says about its usage limits (CUT-292). Read-only; "not reported" is a
// real answer, never a zero.

export interface UsageWindow {
  name: string;
  label: string;
  /** null when the backend sent a reset time or a status but no figure. */
  used_percent: number | null;
  /** Epoch seconds. */
  resets_at: number | null;
  minutes: number | null;
  status: string | null;
  warn: boolean;
}

export type BackendUsage =
  | { reported: false; note: string }
  | { reported: true; at: number | null; windows: UsageWindow[]; warn: boolean };

export interface UsageView {
  local: Record<string, BackendUsage>;
  warn_at_percent: number;
}

/** "resets in 2 h 10 min", "resets in 3 d", or "reset time not given". */
export function resetText(resetsAt: number | null, now = Date.now() / 1000): string {
  if (resetsAt === null) return "reset time not given";
  const seconds = Math.round(resetsAt - now);
  if (seconds <= 0) return "resets now";
  if (seconds < 3600) return `resets in ${Math.max(1, Math.round(seconds / 60))} min`;
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    return minutes > 0 ? `resets in ${hours} h ${minutes} min` : `resets in ${hours} h`;
  }
  return `resets in ${Math.round(seconds / 86400)} d`;
}

/** "82% used, resets in 1 h 5 min": the figure, or the backend's own word when it gave none. */
export function windowText(window: UsageWindow, now = Date.now() / 1000): string {
  const used =
    window.used_percent !== null
      ? `${Math.round(window.used_percent)}% used`
      : window.status === "rejected"
        ? "limit reached"
        : window.status === "allowed_warning"
          ? "near its limit"
          : "usage not given";
  return `${used}, ${resetText(window.resets_at, now)}`;
}

/** Backends with a warning, as words for the top of the card. */
export function warningLines(
  usage: Record<string, BackendUsage> | null | undefined,
  where: string,
  now = Date.now() / 1000,
): string[] {
  const lines: string[] = [];
  for (const [backend, entry] of Object.entries(usage ?? {})) {
    if (!entry.reported || !entry.warn) continue;
    for (const window of entry.windows.filter((w) => w.warn)) {
      lines.push(`${backend} on ${where}: ${window.label} ${windowText(window, now)}`);
    }
  }
  return lines;
}
