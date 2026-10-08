// Pure helpers for the limits settings (V5-limit-settings, ADR-0030): which value applies, what a
// typed number means and whether two sets of overrides are the same. No network, no DOM.

import type { LimitInfo, LimitValues } from "./api";

/** The value in force for `info`: the first layer that sets it (a role's, then the project's),
 * else what the daemon reads when nothing is set. */
export function inForce(info: LimitInfo, ...layers: (LimitValues | undefined)[]): number {
  for (const layer of layers) {
    const value = layer?.[info.key];
    if (value !== undefined) return value;
  }
  return info.default;
}

/** A value with its unit, and what a zero means when it means something: "0 (no limit)". */
export function describeValue(info: LimitInfo, value: number): string {
  if (value === 0 && info.zero_means) return info.zero_means;
  return `${value.toLocaleString("en-GB")} ${info.unit}`;
}

export type ParsedLimit =
  | { kind: "inherit" }
  | { kind: "value"; value: number }
  | { kind: "invalid"; problem: string };

/** What a typed box means: blank inherits, a whole number in range is a value, anything else is
 * a problem to say in words. */
export function parseLimit(info: LimitInfo, text: string): ParsedLimit {
  const trimmed = text.trim();
  if (trimmed === "") return { kind: "inherit" };
  if (!/^\d+$/.test(trimmed)) return { kind: "invalid", problem: "Use a whole number." };
  const value = Number(trimmed);
  if (value < info.minimum) {
    return { kind: "invalid", problem: `At least ${info.minimum}.` };
  }
  if (info.maximum !== null && value > info.maximum) {
    return { kind: "invalid", problem: `At most ${info.maximum}.` };
  }
  return { kind: "value", value };
}

/** `values` with `key` set, or removed when `value` is null. Never mutates. */
export function withLimit(values: LimitValues, key: string, value: number | null): LimitValues {
  const next = { ...values };
  if (value === null) delete next[key];
  else next[key] = value;
  return next;
}

export function sameLimits(a: LimitValues | undefined, b: LimitValues | undefined): boolean {
  const left = a ?? {};
  const right = b ?? {};
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
  return [...keys].every((key) => left[key] === right[key]);
}

/** How many settings are overridden, for a summary line: "2 set". */
export function overrideCount(values: LimitValues | undefined): number {
  return Object.keys(values ?? {}).length;
}
