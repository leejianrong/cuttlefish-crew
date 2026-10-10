import { describe, expect, it } from "vitest";
import { parseHash, routeHash, sameRoute, type Route } from "./route";

const ROUTES: Route[] = [
  { view: "projects" },
  { view: "add" },
  { view: "needs-you" },
  { view: "secrets" },
  { view: "roles" },
  { view: "sprites" },
  { view: "project", id: "abc123", tab: "overview" },
  { view: "project", id: "abc123", tab: "team" },
  { view: "project", id: "abc123", tab: "secrets" },
  { view: "project", id: "a b/c", tab: "needs-you" },
];

describe("route hashes", () => {
  it("round-trips every route", () => {
    for (const route of ROUTES) expect(parseHash(routeHash(route))).toEqual(route);
  });

  it("keeps the common link short", () => {
    expect(routeHash({ view: "project", id: "p1", tab: "overview" })).toBe("#/projects/p1");
    expect(routeHash({ view: "project", id: "p1", tab: "permissions" })).toBe(
      "#/projects/p1/permissions",
    );
  });

  it("lands somewhere usable on anything it does not know", () => {
    for (const hash of ["", "#", "#/", "#/nonsense", "#/projects/%E0%A4%A", "#/projects/"]) {
      expect(parseHash(hash)).toEqual({ view: "projects" });
    }
    expect(parseHash("#/projects/p1/nope")).toEqual({ view: "project", id: "p1", tab: "overview" });
  });

  it("compares by where it points", () => {
    expect(sameRoute({ view: "roles" }, parseHash("#/roles"))).toBe(true);
    expect(sameRoute({ view: "roles" }, { view: "sprites" })).toBe(false);
  });
});
