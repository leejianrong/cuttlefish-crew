// Unit: a sprite frame as one SVG image (pixelimage.ts).

import { describe, expect, it } from "vitest";
import { CUTTLEFISH_FRAMES, paletteFor } from "./cuttlefish";
import { pixelImage } from "./pixelimage";

const decode = (url: string) => decodeURIComponent(url.replace(/^url\("data:image\/svg\+xml,/, "").replace(/"\)$/, ""));

describe("pixelImage", () => {
  it("merges a run of one colour into one rect and skips empty cells", () => {
    const svg = decode(pixelImage(["..AA.B", "......"], { A: "#111111", B: "#222222" }));
    expect(svg).toContain("viewBox='0 0 6 2'");
    expect(svg).toContain("<rect x='2' y='0' width='2' height='1' fill='#111111'/>");
    expect(svg).toContain("<rect x='5' y='0' width='1' height='1' fill='#222222'/>");
    expect(svg.match(/<rect/g)).toHaveLength(2);
  });

  it("is a few dozen rects for the cuttlefish, not one node per pixel", () => {
    const svg = decode(pixelImage(CUTTLEFISH_FRAMES.a, paletteFor("working")));
    expect(svg.match(/<rect/g)!.length).toBeLessThan(50);
  });

  it("returns the cached value for the same frame and palette, and a new one for a new palette", () => {
    const a = pixelImage(CUTTLEFISH_FRAMES.a, paletteFor("done"));
    expect(pixelImage(CUTTLEFISH_FRAMES.a, paletteFor("done"))).toBe(a);
    expect(pixelImage(CUTTLEFISH_FRAMES.a, paletteFor("failed"))).not.toBe(a);
  });
});
