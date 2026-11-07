// Words for what the kill switch did (CUT-186). The daemon sends names; what a person reads is here.

const WHAT: Record<string, string> = { builder: "the builder", team: "the team" };

/** `Stopped the builder and the team on 2 projects.`, or that nothing was running. */
export function killResultText(stopped: Record<string, string[]>): string {
  const projects = Object.values(stopped).filter((names) => names.length > 0);
  if (projects.length === 0) return "Nothing was running.";
  const kinds = [...new Set(projects.flat())].map((name) => WHAT[name] ?? name);
  const what = kinds.length > 1 ? `${kinds.slice(0, -1).join(", ")} and ${kinds.at(-1)}` : kinds[0];
  return `Stopped ${what} on ${projects.length} project${projects.length === 1 ? "" : "s"}.`;
}
