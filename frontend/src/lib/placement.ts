// Where a project runs (ADR-0057): this PC, or a worker machine that may be offline.

import type { ProjectSummary } from "./api";

export interface Where {
  /** What to say beside the project: its machine, or "This PC". */
  text: string;
  /** The machine cannot be reached, so a team started now will be refused. */
  problem: boolean;
}

export function whereItRuns(project: Pick<ProjectSummary, "worker">): Where {
  const worker = project.worker;
  if (!worker) return { text: "This PC", problem: false };
  if (worker.name === null) {
    return { text: "A worker that is no longer paired", problem: true };
  }
  return worker.online
    ? { text: worker.name, problem: false }
    : { text: `${worker.name} (offline)`, problem: true };
}
