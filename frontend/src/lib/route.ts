// Where the dashboard is, as a URL hash (`#/projects/abc/team`), so a reload, the browser's Back
// button and a pasted link land on the same screen. Pure: no window, no history, just the mapping.

export type ProjectTab = "overview" | "needs-you" | "permissions" | "team";

export type Route =
  | { view: "projects" }
  | { view: "add" }
  | { view: "project"; id: string; tab: ProjectTab }
  | { view: "needs-you" }
  | { view: "roles" }
  | { view: "sprites" };

export const PROJECT_TABS: readonly ProjectTab[] = ["overview", "needs-you", "permissions", "team"];

export const HOME: Route = { view: "projects" };

function isTab(value: string): value is ProjectTab {
  return (PROJECT_TABS as readonly string[]).includes(value);
}

/** A hash as a route. Anything it does not recognise is the project list, never an error: a stale
 * or mistyped link should land somewhere usable. */
export function parseHash(hash: string): Route {
  const parts = hash
    .replace(/^#\/?/, "")
    .split("/")
    .filter((part) => part !== "");
  const [view, id, tab] = parts;
  switch (view) {
    case "add":
      return { view: "add" };
    case "needs-you":
      return { view: "needs-you" };
    case "roles":
      return { view: "roles" };
    case "sprites":
      return { view: "sprites" };
    case "projects": {
      if (!id) return HOME;
      let decoded: string;
      try {
        decoded = decodeURIComponent(id);
      } catch {
        return HOME;
      }
      return { view: "project", id: decoded, tab: tab && isTab(tab) ? tab : "overview" };
    }
    default:
      return HOME;
  }
}

/** A route as a hash. The first tab is left out so the common link stays short. */
export function routeHash(route: Route): string {
  switch (route.view) {
    case "projects":
      return "#/projects";
    case "project": {
      const base = `#/projects/${encodeURIComponent(route.id)}`;
      return route.tab === "overview" ? base : `${base}/${route.tab}`;
    }
    default:
      return `#/${route.view}`;
  }
}

export function sameRoute(a: Route, b: Route): boolean {
  return routeHash(a) === routeHash(b);
}
