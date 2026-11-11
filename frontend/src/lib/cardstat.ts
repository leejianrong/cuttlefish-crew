// What a project card says about its goal's branch (CUT-287): the diff counts and the pull request's
// state, from the daemon's cached sweep (`gs` on the project list; single letters because it rides on
// every card of a byte-budgeted list). Pure, so each rule is unit-tested.

/** `f` files, `a` added, `d` removed, `u` uncommitted work, `p` pull request (open, merged, closed),
 * `c` its checks (failing, passing, running). Every key is left out when it has nothing to say. */
export interface CardStat {
  f?: number;
  a?: number;
  d?: number;
  u?: 1;
  p?: "o" | "m" | "c";
  c?: "f" | "p" | "r";
}

const PR: Record<string, string> = { o: "PR open", m: "PR merged", c: "PR closed" };
const CHECKS: Record<string, string> = { f: "checks failing", p: "checks passing", r: "checks running" };

/** One line for the card, or null when there is nothing to show. `attention` is a failing check. */
export function cardStatLine(stat: CardStat | null | undefined): { text: string; attention: boolean } | null {
  if (!stat) return null;
  const parts: string[] = [];
  if (stat.f) {
    parts.push(`${stat.f} file${stat.f === 1 ? "" : "s"} +${stat.a ?? 0} −${stat.d ?? 0}${stat.u ? " (uncommitted)" : ""}`);
  }
  if (stat.p && PR[stat.p]) {
    // Checks only matter while the PR is open.
    parts.push(stat.p === "o" && stat.c && CHECKS[stat.c] ? `${PR[stat.p]}, ${CHECKS[stat.c]}` : PR[stat.p]);
  }
  if (parts.length === 0) return null;
  return { text: parts.join(" · "), attention: stat.p === "o" && stat.c === "f" };
}
