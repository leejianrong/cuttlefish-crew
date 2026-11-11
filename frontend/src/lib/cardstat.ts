// What a project card says about its goal's branch (CUT-287): the diff counts and the pull request's
// state, from the daemon's cached sweep (`gs` on the project list). It is a short positional list,
// because it rides on every card of a byte-budgeted list; trailing items are left off when empty.
// Pure, so each rule is unit-tested.

/** `[files, added, removed, uncommitted (0 or 1), pull request, checks]`: the pull request is `"o"`
 * open, `"m"` merged or `"c"` closed, its checks `"f"` failing, `"p"` passing or `"r"` running. */
export type CardStat = [number, number, number, number?, string?, string?];

const PR: Record<string, string> = { o: "PR open", m: "PR merged", c: "PR closed" };
const CHECKS: Record<string, string> = { f: "checks failing", p: "checks passing", r: "checks running" };

/** One line for the card, or null when there is nothing to show. `attention` is a failing check. */
export function cardStatLine(stat: CardStat | null | undefined): { text: string; attention: boolean } | null {
  if (!Array.isArray(stat)) return null;
  const [files = 0, added = 0, removed = 0, dirty = 0, pr = "", checks = ""] = stat;
  const parts: string[] = [];
  if (files > 0) {
    parts.push(`${files} file${files === 1 ? "" : "s"} +${added} −${removed}${dirty ? " (uncommitted)" : ""}`);
  }
  if (PR[pr]) {
    // Checks only matter while the PR is open.
    parts.push(pr === "o" && CHECKS[checks] ? `${PR[pr]}, ${CHECKS[checks]}` : PR[pr]);
  }
  if (parts.length === 0) return null;
  return { text: parts.join(" · "), attention: pr === "o" && checks === "f" };
}
