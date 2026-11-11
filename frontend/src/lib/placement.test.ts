import { describe, expect, it } from "vitest";
import { whereItRuns } from "./placement";

describe("whereItRuns", () => {
  it("is this PC with no worker", () => {
    expect(whereItRuns({})).toEqual({ text: "This PC", problem: false });
    expect(whereItRuns({ worker: null })).toEqual({ text: "This PC", problem: false });
  });

  it("names the machine and says when it is offline", () => {
    expect(whereItRuns({ worker: { id: "w", name: "Studio laptop", online: true } })).toEqual({
      text: "Studio laptop",
      problem: false,
    });
    expect(whereItRuns({ worker: { id: "w", name: "Studio laptop", online: false } })).toEqual({
      text: "Studio laptop (offline)",
      problem: true,
    });
  });

  it("says so when the worker was removed from the registry", () => {
    expect(whereItRuns({ worker: { id: "w", name: null, online: false } }).problem).toBe(true);
  });
});
