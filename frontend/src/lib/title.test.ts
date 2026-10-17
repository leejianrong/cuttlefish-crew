import { describe, expect, it } from "vitest";
import { BASE_TITLE, tabTitle } from "./title";

describe("tabTitle", () => {
  it("is the plain name with nothing waiting", () => {
    expect(tabTitle(0)).toBe(BASE_TITLE);
  });

  it("puts the count first so it survives a short tab label", () => {
    expect(tabTitle(3)).toBe("(3) cuttlefish-crew");
  });
});
