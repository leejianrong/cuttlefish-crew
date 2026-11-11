// Browser notifications and the unread bell (CUT-290). The dashboard already polls requests and
// projects; this notices what is new between two polls. It uses the Notification API, so it only
// fires while a dashboard tab is open: there is no push server (ADR-0037, ADR-0043). The text is
// content-free: it names no project, command or question.

import type { NeedsYouRequest, ProjectSummary } from "./api";
import { projectState, type ProjectState } from "./projectstate";

export interface NotifyEvent {
  id: string;
  kind: "needs-you" | "done";
  at: number;
}

/** What each notice says, with no project name and no request text. */
export function eventText(kind: NotifyEvent["kind"], count: number): string {
  if (kind === "needs-you") return count === 1 ? "A project needs you" : `${count} projects need you`;
  return count === 1 ? "A project finished" : `${count} projects finished`;
}

/** One line per kind for a batch of events, e.g. for the notification body. */
export function summarise(events: readonly NotifyEvent[]): string {
  return (["needs-you", "done"] as const)
    .map((kind) => [kind, events.filter((e) => e.kind === kind).length] as const)
    .filter(([, count]) => count > 0)
    .map(([kind, count]) => eventText(kind, count))
    .join(". ");
}

/** Remembers what the last poll saw. The first poll of each only sets the baseline, so opening the
 * dashboard on a busy fleet is not a burst of notices. */
export function createTracker(now: () => number = Date.now) {
  let seenRequests: Set<string> | null = null;
  let states: Map<string, ProjectState> | null = null;
  return {
    requests(list: readonly NeedsYouRequest[]): NotifyEvent[] {
      const ids = new Set(list.map((r) => r.id));
      const fresh = seenRequests ? list.filter((r) => !seenRequests!.has(r.id)) : [];
      seenRequests = ids;
      return fresh.map((r) => ({ id: `req:${r.id}`, kind: "needs-you", at: now() }));
    },
    /** A project that was not done and now is. `waitingBy` is requests waiting per project id. */
    projects(list: readonly ProjectSummary[], waitingBy: Readonly<Record<string, number>>): NotifyEvent[] {
      const next = new Map(list.map((p) => [p.id, projectState(p, waitingBy[p.id] ?? 0).state] as const));
      const finished = states
        ? list.filter((p) => next.get(p.id) === "done" && states!.has(p.id) && states!.get(p.id) !== "done")
        : [];
      states = next;
      return finished.map((p) => ({ id: `done:${p.id}:${p.last_event_at ?? ""}`, kind: "done", at: now() }));
    },
  };
}

/** Newest first, at most `keep`. */
export function addEvents(
  all: readonly NotifyEvent[],
  fresh: readonly NotifyEvent[],
  keep = 20,
): NotifyEvent[] {
  return [...fresh, ...all].slice(0, keep);
}

export function unreadCount(all: readonly NotifyEvent[], readAt: number): number {
  return all.filter((e) => e.at > readAt).length;
}

export type Permission = "unsupported" | "default" | "granted" | "denied";

export function permission(): Permission {
  return typeof Notification === "undefined" ? "unsupported" : Notification.permission;
}

const MUTE_KEY = "cuttlefish.notifications-muted";

/** Per browser: muted unless the person turned notifications on here. Safe to lose. */
export function loadOn(): boolean {
  try {
    return localStorage.getItem(MUTE_KEY) === "on";
  } catch {
    return false;
  }
}

export function saveOn(on: boolean): void {
  try {
    localStorage.setItem(MUTE_KEY, on ? "on" : "off");
  } catch {
    // Lasts until the page is closed.
  }
}

/** Ask for permission. Call only from a click: browsers refuse a prompt that is not a gesture. */
export async function askPermission(): Promise<Permission> {
  if (typeof Notification === "undefined") return "unsupported";
  return await Notification.requestPermission();
}

/** Show one notice for a batch, only when the person turned it on and the browser allows it. A
 * visible tab needs no system notice: the bell is enough. */
export function show(events: readonly NotifyEvent[], on: boolean): void {
  if (!on || events.length === 0 || permission() !== "granted") return;
  if (typeof document !== "undefined" && document.visibilityState === "visible") return;
  try {
    new Notification("cuttlefish-crew", { body: summarise(events), tag: "cuttlefish-crew" });
  } catch {
    // Some browsers only allow notifications from a service worker; the bell still counts.
  }
}

/** What the panel says about whether a notice can reach the person. */
export function reach(perm: Permission, on: boolean): string {
  if (perm === "unsupported") return "This browser cannot show notifications.";
  if (perm === "denied") {
    return "Notifications are blocked for this site. Allow them in the browser's site settings, then come back.";
  }
  if (perm === "default") return "Desktop notices are off. Turn them on to be told when you are in another tab.";
  return on
    ? "Desktop notices are on. They only fire while this dashboard is open in a tab: close it and nothing reaches you."
    : "Desktop notices are muted on this browser. The bell still counts.";
}
