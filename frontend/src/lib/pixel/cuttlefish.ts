// A small pixel-art cuttlefish mascot -- one character per role, on-brand for
// "cuttlefish-crew" rather than a generic humanoid/robot sprite (slice D2,
// ADR-0009's own "a rendering layer on an already-proven API"). Two frames
// (`a`/`b`) differ only in which tentacles are down, alternated while a role is
// "working" to read as a gentle wiggle; every other status renders frame `a`
// statically, tinted by `paletteFor`.
//
// Authored as row-strings, not raw pixel coordinates -- a shape this size is far
// easier to eyeball-proofread as ASCII art than as a list of (x, y) pairs.

import type { RoleStatus } from "../api";

export type PixelFrame = readonly string[];

const FRAME_A: PixelFrame = [
  "..............",
  "....HHHHHH....",
  "...HHHHHHHH...",
  "..HHHHHHHHHH..",
  "..HEEH..HEEH..",
  "..HEPH..HEPH..",
  "..HHHHHHHHHH..",
  "..HHHHHHHHHH..",
  "..HHHHHHHHHH..",
  "T.T.T.T.T.T.T.",
  ".T.T.T.T.T.T.T",
  "..............",
];

const FRAME_B: PixelFrame = [
  ...FRAME_A.slice(0, 9),
  ".T.T.T.T.T.T.T",
  "T.T.T.T.T.T.T.",
  "..............",
];

export const CUTTLEFISH_FRAMES: Record<"a" | "b", PixelFrame> = { a: FRAME_A, b: FRAME_B };

// Eyes and pupils stay constant -- only the head/tentacle tint carries status,
// so every state reads as "the same character," not a different creature.
const CONSTANT_PALETTE: Record<string, string> = { E: "#fdf6ec", P: "#191521" };

const TINTS: Record<RoleStatus, { H: string; T: string }> = {
  queued: { H: "#5b6270", T: "#454b59" },
  working: { H: "#3fd7c4", T: "#22a793" },
  blocked: { H: "#ffb15e", T: "#d98a3d" },
  done: { H: "#5bd88a", T: "#2f9f5c" },
  failed: { H: "#ff6b5e", T: "#c94236" },
  stopped: { H: "#7d8594", T: "#5d6573" },
};

export function paletteFor(status: RoleStatus): Record<string, string> {
  return { ...CONSTANT_PALETTE, ...TINTS[status] };
}

// `null` for "working": the wiggle animation itself already signals activity,
// so no glyph competes with it for attention.
export const STATUS_GLYPH: Record<RoleStatus, string | null> = {
  queued: "z",
  working: null,
  blocked: "!",
  done: "✓",
  failed: "×",
  stopped: "■",
};
