// A pixel-art frame as one SVG image, so a sprite is a single element instead of one per pixel.
// Horizontal runs of one colour share a rect; the result is cached per frame and palette, so a
// sprite that flips frames or changes status only swaps a style value.

import type { PixelFrame } from "./cuttlefish";

const cache = new Map<string, string>();

/** The SVG for `frame` as a `url("data:...")` value, for `background-image`. */
export function pixelImage(frame: PixelFrame, palette: Readonly<Record<string, string>>): string {
  const key = `${frame.join("|")}#${JSON.stringify(palette)}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const rects: string[] = [];
  frame.forEach((row, y) => {
    for (let x = 0; x < row.length; ) {
      const cell = row[x];
      let end = x + 1;
      while (end < row.length && row[end] === cell) end++;
      const colour = cell === "." ? undefined : palette[cell];
      if (colour) rects.push(`<rect x='${x}' y='${y}' width='${end - x}' height='1' fill='${colour}'/>`);
      x = end;
    }
  });
  const svg =
    `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 ${frame[0]?.length ?? 0} ${frame.length}' ` +
    `shape-rendering='crispEdges'>${rects.join("")}</svg>`;
  const url = `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
  cache.set(key, url);
  return url;
}
