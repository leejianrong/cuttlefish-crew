import { describe, expect, it } from "vitest";
import type { Artifact } from "./api";
import {
  FRAME_CSP,
  FRAME_SANDBOX,
  ago,
  agoIso,
  canvasFiles,
  htmlFrameDoc,
  isViewable,
  manifestFile,
  parsePresentation,
  refusalText,
  sameArtifacts,
  sizeText,
} from "./artifacts";

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

describe("the sandboxed frame (CUT-175)", () => {
  it("puts the policy before the agent's markup, which cannot load or send anything", () => {
    const doc = htmlFrameDoc("<p>hi</p><script>fetch('https://x')</script>");
    expect(doc.startsWith("<!doctype html><meta http-equiv=\"Content-Security-Policy\"")).toBe(true);
    expect(doc.indexOf("Content-Security-Policy")).toBeLessThan(doc.indexOf("<p>hi</p>"));
    expect(FRAME_CSP).toContain("default-src 'none'");
    expect(FRAME_CSP).not.toMatch(/connect-src|https?:/);
    expect(FRAME_CSP).toContain("form-action 'none'");
  });

  it("lets scripts run and nothing else out: no same origin, no navigation, no forms", () => {
    expect(FRAME_SANDBOX).toBe("allow-scripts");
    expect(FRAME_SANDBOX).not.toContain("allow-same-origin");
    expect(FRAME_SANDBOX).not.toContain("top-navigation");
  });

  it("shows what can be shown and leaves the plan to the rail", () => {
    const file = (path: string, kind: Artifact["kind"]): Artifact => ({ path, name: path, kind, size: 1, modified: 1 });
    const shown = canvasFiles([
      file("a.png", "image"),
      file("plan.md", "markdown"),
      file("x.bin", "other"),
      file("v.mp4", "video"),
      file("d.html", "html"),
    ]);
    expect(shown.map((f) => f.path)).toEqual(["a.png", "v.mp4", "d.html"]);
    expect(canvasFiles(Array.from({ length: 20 }, (_, i) => file(`${i}.png`, "image")))).toHaveLength(8);
    expect(isViewable("other")).toBe(false);
  });
});

describe("present manifest (CUT-199)", () => {
  const f = (path: string, kind: Artifact["kind"] = "html"): Artifact => ({
    path, name: path.split("/").pop() ?? path, kind, size: 1, modified: 1,
  });
  const files = [f("present.json", "manifest"), f("slides/a.html"), f("doc/b.md", "markdown"), f("run.sh", "other")];

  it("finds the manifest only at the top level", () => {
    expect(manifestFile(files)?.path).toBe("present.json");
    expect(manifestFile([f("sub/present.json", "other")])).toBeNull();
  });
  it("keeps order, text and a title, and skips what cannot be shown", () => {
    const text = JSON.stringify({
      title: " Demo ",
      items: [
        { path: "doc/b.md", say: "second file first" },
        { path: "./slides/a.html" },
        { path: "gone.html", say: "x" },
        { path: "run.sh", say: "x" },
        "nonsense",
      ],
    });
    const plan = parsePresentation(text, files);
    expect(plan?.title).toBe("Demo");
    expect(plan?.steps.map((s) => [s.path, s.say])).toEqual([
      ["doc/b.md", "second file first"],
      ["slides/a.html", ""],
    ]);
    expect(plan?.skipped).toBe(3);
  });
  it("refuses bad JSON and the wrong shape instead of throwing", () => {
    expect(parsePresentation("{nope", files)).toBeNull();
    expect(parsePresentation("[]", files)).toBeNull();
    expect(parsePresentation('{"items": "x"}', files)).toBeNull();
    expect(parsePresentation('{"items": []}', files)?.title).toBe("Walk-through");
  });
  it("caps the steps and the speaker text", () => {
    const items = Array.from({ length: 60 }, () => ({ path: "doc/b.md", say: "y".repeat(2000) }));
    const plan = parsePresentation(JSON.stringify({ items }), files);
    expect(plan?.steps).toHaveLength(50);
    expect(plan?.skipped).toBe(10);
    expect(plan?.steps[0].say).toHaveLength(1000);
  });
});
