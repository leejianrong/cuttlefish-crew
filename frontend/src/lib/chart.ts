// The small chart an agent describes in a ```chart block (CUT-114). A spec in, drawing geometry
// out, so the Svelte side only places numbers it was given and the maths is testable.

export type ChartKind = "bar" | "line";

export interface ChartSpec {
  kind: ChartKind;
  title: string;
  labels: string[];
  series: { name: string; values: number[] }[];
}

const MAX_LABELS = 60;
const MAX_SERIES = 6;

/** The spec in a ```chart block, or `null` when it is not usable (the page then shows the code). */
export function parseChartSpec(text: string): ChartSpec | null {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof data !== "object" || data === null || Array.isArray(data)) return null;
  const raw = data as Record<string, unknown>;
  const kind = raw.type === undefined ? "bar" : raw.type;
  if (kind !== "bar" && kind !== "line") return null;
  const labels = raw.labels;
  if (!Array.isArray(labels) || labels.length === 0 || labels.length > MAX_LABELS) return null;
  const names = labels.map((l) => (typeof l === "string" || typeof l === "number" ? String(l) : null));
  if (names.includes(null)) return null;
  // `values` is shorthand for one unnamed series.
  const given = Array.isArray(raw.series) ? raw.series : raw.values !== undefined ? [{ name: "", values: raw.values }] : null;
  if (!given || given.length === 0 || given.length > MAX_SERIES) return null;
  const series: ChartSpec["series"] = [];
  for (const s of given) {
    if (typeof s !== "object" || s === null) return null;
    const { name, values } = s as { name?: unknown; values?: unknown };
    if (!Array.isArray(values) || values.length !== labels.length) return null;
    if (!values.every((v) => typeof v === "number" && Number.isFinite(v))) return null;
    series.push({ name: typeof name === "string" ? name.slice(0, 40) : "", values: values as number[] });
  }
  return {
    kind,
    title: typeof raw.title === "string" ? raw.title.slice(0, 120) : "",
    labels: (names as string[]).map((l) => l.slice(0, 40)),
    series,
  };
}

export const CHART_W = 600;
export const CHART_H = 300;
const LEFT = 48;
const RIGHT = 12;
const TOP = 12;
const BOTTOM = 36;

/** A round step (1, 2, 5 times a power of ten) near `rough`. */
export function niceStep(rough: number): number {
  const power = 10 ** Math.floor(Math.log10(rough));
  const fraction = rough / power;
  const factor = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return factor * power;
}

export interface ChartLayout {
  ticks: { value: number; y: number }[];
  zeroY: number;
  bars: { x: number; y: number; w: number; h: number; series: number; label: string; value: number }[];
  lines: { points: { x: number; y: number }[]; series: number }[];
  labels: { x: number; text: string }[];
  plot: { left: number; right: number; top: number; bottom: number };
}

export function layoutChart(spec: ChartSpec): ChartLayout {
  const all = spec.series.flatMap((s) => s.values);
  const lo0 = Math.min(0, ...all);
  const hi0 = Math.max(0, ...all);
  const step = niceStep((hi0 - lo0 || 1) / 4);
  const lo = Math.floor(lo0 / step) * step;
  const hi = Math.max(Math.ceil(hi0 / step) * step, lo + step);
  const plot = { left: LEFT, right: CHART_W - RIGHT, top: TOP, bottom: CHART_H - BOTTOM };
  const y = (v: number) => plot.bottom - ((v - lo) / (hi - lo)) * (plot.bottom - plot.top);
  const ticks: ChartLayout["ticks"] = [];
  for (let v = lo; v <= hi + step / 2; v += step) {
    const value = Math.round(v / step) * step;
    ticks.push({ value, y: y(value) });
  }
  const n = spec.labels.length;
  const band = (plot.right - plot.left) / n;
  const center = (i: number) => plot.left + band * (i + 0.5);
  const every = Math.ceil(n / 10);
  const layout: ChartLayout = {
    ticks,
    zeroY: y(0),
    bars: [],
    lines: [],
    labels: spec.labels.flatMap((text, i) => (i % every === 0 ? [{ x: center(i), text }] : [])),
    plot,
  };
  if (spec.kind === "bar") {
    const group = band * 0.7;
    const width = group / spec.series.length;
    spec.series.forEach((s, k) => {
      s.values.forEach((value, i) => {
        const top = y(Math.max(value, 0));
        const bottom = y(Math.min(value, 0));
        layout.bars.push({
          x: center(i) - group / 2 + width * k,
          y: top,
          w: Math.max(width - 1, 1),
          h: Math.max(bottom - top, value === 0 ? 0 : 1),
          series: k,
          label: spec.labels[i],
          value,
        });
      });
    });
  } else {
    spec.series.forEach((s, k) => {
      layout.lines.push({ points: s.values.map((v, i) => ({ x: center(i), y: y(v) })), series: k });
    });
  }
  return layout;
}

/** A short plain-text description for a screen reader, since the drawing itself is not read. */
export function describeChart(spec: ChartSpec): string {
  const head = spec.title || `${spec.kind} chart`;
  const rows = spec.series.map((s) => {
    const cells = s.values.map((v, i) => `${spec.labels[i]} ${v}`).join(", ");
    return s.name ? `${s.name}: ${cells}` : cells;
  });
  return `${head}. ${rows.join(". ")}`;
}
