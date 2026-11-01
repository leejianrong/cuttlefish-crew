// A git project's team works on a branch of its own, one more per role, merged at the end of each round
// (CUT-239, ADR-0054). This reads that out of a team's journal for the Team tab.

import type { EpisodicEventView } from "./api";

export type MergeStatus = "merged" | "nothing" | "conflict" | "pending";

export interface RoleBranch {
  role: string;
  branch: string;
  status: MergeStatus;
  files: string[];
}

export interface TeamBranch {
  branch: string;
  roles: RoleBranch[];
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

/** The latest team branch in these events, with each role's latest merge result after it, or null
 * when the team had no branch (not a git project, or fewer than two kopicode roles). */
export function teamBranch(events: readonly EpisodicEventView[]): TeamBranch | null {
  let made: EpisodicEventView | null = null;
  for (const event of events) if (event.event_type === "TeamBranchMade") made = event;
  if (made === null) return null;
  const branches = (made.payload.roles ?? {}) as Record<string, string>;
  const merges = new Map<string, EpisodicEventView>();
  for (const event of events) {
    if (event.event_type === "RoleMergeReported" && event.seq > made.seq) {
      merges.set(String(event.payload.role), event);
    }
  }
  return {
    branch: String(made.payload.branch),
    roles: Object.entries(branches).map(([role, branch]) => {
      const merge = merges.get(role);
      const status = String(merge?.payload.status ?? "pending");
      return {
        role,
        branch,
        status: (["merged", "nothing", "conflict"].includes(status) ? status : "pending") as MergeStatus,
        files: strings(merge?.payload.files),
      };
    }),
  };
}

export function mergeLabel(status: MergeStatus): string {
  switch (status) {
    case "merged":
      return "Merged";
    case "nothing":
      return "No changes";
    case "conflict":
      return "Conflict";
    default:
      return "Working";
  }
}
