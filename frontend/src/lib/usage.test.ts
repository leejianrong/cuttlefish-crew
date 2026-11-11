import { describe, expect, it } from "vitest";
import { resetText, warningLines, windowText, type UsageWindow } from "./usage";

const NOW = 1_000_000;
const window = (over: Partial<UsageWindow> = {}): UsageWindow => ({
  name: "five_hour",
  label: "5 hours",
  used_percent: 82,
  resets_at: NOW + 3900,
  minutes: null,
  status: null,
  warn: true,
  ...over,
});

describe("usage text", () => {
  it("says when a window resets", () => {
    expect(resetText(null, NOW)).toBe("reset time not given");
    expect(resetText(NOW - 5, NOW)).toBe("resets now");
    expect(resetText(NOW + 600, NOW)).toBe("resets in 10 min");
    expect(resetText(NOW + 3900, NOW)).toBe("resets in 1 h 5 min");
    expect(resetText(NOW + 7200, NOW)).toBe("resets in 2 h");
    expect(resetText(NOW + 3 * 86400, NOW)).toBe("resets in 3 d");
  });

  it("gives the figure, or the backend's own word when it gave none", () => {
    expect(windowText(window(), NOW)).toBe("82% used, resets in 1 h 5 min");
    expect(windowText(window({ used_percent: null, status: "rejected" }), NOW)).toContain("limit reached");
    expect(windowText(window({ used_percent: null, status: "allowed_warning" }), NOW)).toContain("near its limit");
    expect(windowText(window({ used_percent: null }), NOW)).toContain("usage not given");
  });

  it("lists only the windows that warn, and nothing for a backend that did not report", () => {
    const usage = {
      codex: { reported: true as const, at: 1, warn: true, windows: [window(), window({ warn: false, name: "x" })] },
      kopicode: { reported: false as const, note: "Does not report usage." },
    };
    expect(warningLines(usage, "this PC", NOW)).toEqual(["codex on this PC: 5 hours 82% used, resets in 1 h 5 min"]);
    expect(warningLines(null, "this PC")).toEqual([]);
  });
});
