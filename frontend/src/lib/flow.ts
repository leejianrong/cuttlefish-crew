// The flowchart an agent writes in a ```mermaid block (CUT-278). Only a small subset of Mermaid's
// flowchart syntax is read: text in, drawing geometry out, drawn as inline SVG from numbers and
// strings, so no library and no markup is involved. Anything outside the subset (another diagram
// type, subgraph, styling, a direction other than TD/TB/LR) returns `null` and the page shows the code.

export type Shape = "box" | "round" | "diamond";

export interface FlowNode {
  id: string;
  text: string;
  shape: Shape;
}

export interface FlowEdge {
  from: string;
  to: string;
  label: string;
  style: "solid" | "dotted" | "thick" | "plain";
}

export interface FlowSpec {
  down: boolean;
  nodes: FlowNode[];
  edges: FlowEdge[];
}

const MAX_NODES = 40;
const MAX_EDGES = 80;
const MAX_TEXT = 40;

const NODE = /^([A-Za-z_]\w*)(?:\[\(?"?([^\]"]*)"?\)?\]|\(\(?"?([^)"\[\]]*)"?\)?\)|\{"?([^}"]*)"?\})?/;
const LINK = /^\s*(-->|---|-\.->|==>)\s*(?:\|"?([^|"]*)"?\|)?\s*/;

function parseNode(s: string): { node: FlowNode; rest: string } | null {
  const m = NODE.exec(s);
  if (!m) return null;
  const [whole, id, box, round, diamond] = m;
  const shape: Shape = diamond !== undefined ? "diamond" : round !== undefined ? "round" : "box";
  const text = (box ?? round ?? diamond ?? "").trim().slice(0, MAX_TEXT);
  return { node: { id, text, shape }, rest: s.slice(whole.length) };
}

/** The flowchart in a ```mermaid block, or `null` when it is not one this reader draws. */
export function parseFlow(text: string): FlowSpec | null {
  const lines = text
    .split("\n")
    .flatMap((l) => l.split(";"))
    .map((l) => l.trim())
    .filter((l) => l !== "" && !l.startsWith("%%"));
  const head = /^(?:flowchart|graph)(?:\s+(TD|TB|LR))?$/.exec(lines[0] ?? "");
  if (!head) return null;
  const nodes = new Map<string, FlowNode>();
  const edges: FlowEdge[] = [];
  const see = (n: FlowNode) => {
    const old = nodes.get(n.id);
    // A later mention with text or a shape (`A --> B[Done]`) completes an earlier bare id.
    if (!old || (n.text !== "" && old.text === "")) nodes.set(n.id, n);
    return n.id;
  };
  for (const line of lines.slice(1)) {
    let cur = parseNode(line);
    if (!cur) return null;
    let from = see(cur.node);
    let rest = cur.rest;
    while (rest.trim() !== "") {
      const link = LINK.exec(rest);
      if (!link) return null;
      cur = parseNode(rest.slice(link[0].length));
      if (!cur) return null;
      const to = see(cur.node);
      const style = link[1] === "-.->" ? "dotted" : link[1] === "==>" ? "thick" : link[1] === "---" ? "plain" : "solid";
      edges.push({ from, to, label: (link[2] ?? "").trim().slice(0, MAX_TEXT), style });
      from = to;
      rest = cur.rest;
    }
  }
  if (nodes.size === 0 || nodes.size > MAX_NODES || edges.length > MAX_EDGES) return null;
  return { down: head[1] !== "LR", nodes: [...nodes.values()].map((n) => ({ ...n, text: n.text || n.id })), edges };
}

const H = 40;
const GAP_RANK = 56;
const GAP_NODE = 24;
const PAD = 12;

export interface PlacedNode extends FlowNode {
  x: number;
  y: number;
  w: number;
  h: number;
}
export interface PlacedEdge extends FlowEdge {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}
export interface FlowLayout {
  width: number;
  height: number;
  nodes: PlacedNode[];
  edges: PlacedEdge[];
}

/** Where the ray from the centre of `a` towards `(tx, ty)` leaves a's box. */
function clip(a: PlacedNode, tx: number, ty: number): { x: number; y: number } {
  const dx = tx - a.x;
  const dy = ty - a.y;
  if (dx === 0 && dy === 0) return { x: a.x, y: a.y };
  const t = Math.min(dx === 0 ? Infinity : a.w / 2 / Math.abs(dx), dy === 0 ? Infinity : a.h / 2 / Math.abs(dy));
  return { x: a.x + dx * t, y: a.y + dy * t };
}

export function layoutFlow(spec: FlowSpec): FlowLayout {
  const n = spec.nodes.length;
  // Longest-path ranks over the edges that do not close a cycle (found by a depth-first walk in
  // source order), so a loop back to an earlier node does not push that node down.
  const state = new Map<string, 1 | 2>();
  const forward: FlowEdge[] = [];
  const walk = (id: string) => {
    state.set(id, 1);
    for (const e of spec.edges) {
      if (e.from !== id || e.from === e.to) continue;
      const seen = state.get(e.to);
      if (seen === 1) continue;
      forward.push(e);
      if (!seen) walk(e.to);
    }
    state.set(id, 2);
  };
  for (const nd of spec.nodes) if (!state.has(nd.id)) walk(nd.id);
  const rank = new Map(spec.nodes.map((nd) => [nd.id, 0]));
  for (let pass = 0; pass < n; pass++) {
    for (const e of forward) rank.set(e.to, Math.max(rank.get(e.to) ?? 0, (rank.get(e.from) ?? 0) + 1));
  }
  const rows: FlowNode[][] = [];
  for (const nd of spec.nodes) (rows[rank.get(nd.id) ?? 0] ??= []).push(nd);
  const size = (nd: FlowNode) => ({
    w: Math.max(56, nd.text.length * 7.5 + (nd.shape === "diamond" ? 48 : 28)),
    h: nd.shape === "diamond" ? H + 8 : H,
  });
  // Space along a rank; the rank axis is vertical for TD and horizontal for LR.
  const span = rows.map((r) => (r ?? []).reduce((s, nd) => s + (spec.down ? size(nd).w : size(nd).h) + GAP_NODE, -GAP_NODE));
  const widest = Math.max(...span);
  const rankSize = rows.map((r) => Math.max(...(r ?? []).map((nd) => (spec.down ? size(nd).h : size(nd).w))));
  const placed = new Map<string, PlacedNode>();
  let offset = PAD;
  rows.forEach((r, k) => {
    let pos = PAD + (widest - span[k]) / 2;
    for (const nd of r ?? []) {
      const { w, h } = size(nd);
      const along = spec.down ? w : h;
      const across = rankSize[k] / 2 + offset;
      const mid = pos + along / 2;
      placed.set(nd.id, { ...nd, w, h, x: spec.down ? mid : across, y: spec.down ? across : mid });
      pos += along + GAP_NODE;
    }
    offset += rankSize[k] + GAP_RANK;
  });
  const total = offset - GAP_RANK + PAD;
  const side = widest + 2 * PAD;
  const edges = spec.edges
    .filter((e) => e.from !== e.to)
    .map((e) => {
      const a = placed.get(e.from) as PlacedNode;
      const b = placed.get(e.to) as PlacedNode;
      const p1 = clip(a, b.x, b.y);
      const p2 = clip(b, a.x, a.y);
      return { ...e, x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y };
    });
  return { width: spec.down ? side : total, height: spec.down ? total : side, nodes: [...placed.values()], edges };
}

export function describeFlow(spec: FlowSpec): string {
  const name = (id: string) => spec.nodes.find((nd) => nd.id === id)?.text || id;
  return `Flowchart: ${spec.edges.map((e) => `${name(e.from)} to ${name(e.to)}${e.label ? ` (${e.label})` : ""}`).join(", ") || spec.nodes.map((nd) => nd.text || nd.id).join(", ")}`;
}
