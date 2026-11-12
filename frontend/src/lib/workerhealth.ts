// What a worker machine says about its capacity (CUT-208). A warning only: it never blocks a placement.

import type { WorkerInfo } from "./api";

const GIB = 1024 ** 3;

/** "12 GB free of 500 GB", or "" when the machine did not say. */
export function diskText(health: WorkerInfo["health"]): string {
  if (!health || health.disk_free === undefined || health.disk_total === undefined) return "";
  const gb = (bytes: number) => (bytes / GIB >= 10 ? Math.round(bytes / GIB) : Math.round((bytes / GIB) * 10) / 10);
  return `${gb(health.disk_free)} GB free of ${gb(health.disk_total)} GB`;
}

export function loadText(health: WorkerInfo["health"]): string {
  if (!health || health.load1 === undefined) return "";
  return health.cpus ? `load ${health.load1} on ${health.cpus} cpus` : `load ${health.load1}`;
}

export function agentsText(health: WorkerInfo["health"]): string {
  if (!health || health.agents === undefined) return "";
  return health.agents === 1 ? "1 team running" : `${health.agents} teams running`;
}

/** The warnings in words, for a machine that is online. */
export function warnings(worker: Pick<WorkerInfo, "flags" | "health">): string[] {
  const found: string[] = [];
  if (worker.flags?.includes("disk")) found.push(`Disk nearly full (${diskText(worker.health)})`);
  if (worker.flags?.includes("load")) found.push(`Busy (${loadText(worker.health)})`);
  return found;
}

/** A short tag for the machine picker: " (offline)", " (disk low)", " (busy)" or nothing. */
export function pickerNote(worker: Pick<WorkerInfo, "online" | "flags">): string {
  if (!worker.online) return " (offline)";
  const parts = [worker.flags?.includes("disk") ? "disk low" : "", worker.flags?.includes("load") ? "busy" : ""];
  const text = parts.filter(Boolean).join(", ");
  return text ? ` (${text})` : "";
}

/** What differs from the worker profile, in words; "" when it matches or the machine did not say. */
export function driftText(worker: Pick<WorkerInfo, "drift">): string {
  return worker.drift?.length ? `Differs from the expected setup: ${worker.drift.join(", ")}` : "";
}
