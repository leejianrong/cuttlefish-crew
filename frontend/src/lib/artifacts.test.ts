import { describe, expect, it } from "vitest";
import { ago, agoIso, refusalText, sameArtifacts, sizeText } from "./artifacts";

const file = (path: string, modified: number) => ({
  path, name: path, kind: "markdown" as const, size: 1, modified,
});

describe("artifacts helpers", () => {
  it("says how long ago", () => {
    expect(ago(1000, 1010)).toBe("just now");
    expect(ago(1000, 1000 + 5 * 60)).toBe("5 min ago");
    expect(ago(1000, 1000 + 3 * 3600)).toBe("3 h ago");
    expect(ago(1000, 1000 + 2 * 86400)).toBe("2 d ago");
  });
  it("says how long ago for the daemon's ISO timestamps", () => {
    const at = Date.parse("2026-10-19T02:00:00+00:00") / 1000;
    expect(agoIso("2026-10-19T02:00:00+00:00", at + 5)).toBe("just now");
    expect(agoIso("2026-10-19T02:00:00+00:00", at + 120)).toBe("2 min ago");
    expect(agoIso(null, at)).toBe("");
    expect(agoIso("not a date", at)).toBe("");
  });
  it("formats sizes", () => {
    expect(sizeText(10)).toBe("10 B");
    expect(sizeText(2048)).toBe("2 KB");
    expect(sizeText(3 * 1024 * 1024)).toBe("3.0 MB");
  });
  it("compares lists by path, time and size", () => {
    expect(sameArtifacts([file("a", 1)], [file("a", 1)])).toBe(true);
    expect(sameArtifacts([file("a", 1)], [file("a", 2)])).toBe(false);
    expect(sameArtifacts([file("a", 1)], [])).toBe(false);
  });
  it("explains a refusal", () => {
    expect(refusalText(413)).toMatch(/too large/);
    expect(refusalText(415)).toMatch(/not shown/);
  });
});
