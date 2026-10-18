// Draws each view in model.mjs to an SVG in docs-site/assets/architecture/, laid out by ELK (layered, orthogonal
// edges, nested boundaries). Run from this folder: `npm install && node render.mjs`, or `make diagrams`.
// Pass a view id to draw only that one. Text is wrapped by an estimate of its width, so boxes are
// sized by that estimate, not by a browser measuring them.
import ELK from "elkjs/lib/elk.bundled.js";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { views } from "./model.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "..", "..", "docs-site", "assets", "architecture");
mkdirSync(outDir, { recursive: true });
const elk = new ELK();

const NODE_W = 250;
const CHAR = { name: 8.0, tech: 5.9, desc: 6.3, edge: 6.0 };
const LINE = { name: 19, tech: 15, desc: 15, edge: 14 };
const KIND_LABEL = {
  person: "Person", system: "Software System", ext: "Software System",
  container: "Container", db: "Container", "ext-container": "Container", "ext-db": "Container",
  component: "Component",
};
const LEAF = new Set(["person", "system", "ext", "container", "db", "ext-container", "ext-db", "component"]);

function wrap(text, maxChars) {
  const lines = [];
  let line = "";
  for (const word of text.split(/\s+/).filter(Boolean)) {
    // A word longer than a line (a path) is split rather than allowed to overflow its box.
    let w = word;
    while (w.length > maxChars) {
      if (line) { lines.push(line); line = ""; }
      lines.push(w.slice(0, maxChars));
      w = w.slice(maxChars);
    }
    if (!line) line = w;
    else if ((line + " " + w).length <= maxChars) line += " " + w;
    else { lines.push(line); line = w; }
  }
  if (line) lines.push(line);
  return lines;
}

// Stores are drawn narrower so a row of five stays readable at the width a docs page gives it.
const widthOf = (n) => (n.kind === "db" || n.kind === "ext-db" ? 196 : NODE_W);

function leafText(n) {
  const inner = widthOf(n) - 24;
  const tech = n.tech ? `[${KIND_LABEL[n.kind]}: ${n.tech}]` : `[${KIND_LABEL[n.kind]}]`;
  return {
    name: wrap(n.name, Math.floor(inner / CHAR.name)),
    tech: wrap(tech, Math.floor(inner / CHAR.tech)),
    desc: n.desc ? wrap(n.desc, Math.floor(inner / CHAR.desc)) : [],
  };
}

function leafSize(n) {
  const t = leafText(n);
  const h = 14 + t.name.length * LINE.name + t.tech.length * LINE.tech + (t.desc.length ? 6 : 0) + t.desc.length * LINE.desc + 12;
  return { width: widthOf(n), height: Math.max(h, 70) };
}

function edgeLabel(label, tech) {
  const lines = wrap(label, 30);
  if (tech) lines.push(...wrap(`[${tech}]`, 30));
  const width = Math.max(...lines.map((l) => l.length)) * CHAR.edge + 12;
  return { lines, width, height: lines.length * LINE.edge + 6 };
}

function toElk(node, index) {
  const out = { id: node.id };
  if (LEAF.has(node.kind)) {
    Object.assign(out, leafSize(node));
  } else {
    out.children = node.children.map((c) => toElk(c, index));
    out.layoutOptions = { "elk.padding": "[top=46,left=22,bottom=22,right=22]" };
  }
  index.set(node.id, node);
  return out;
}

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const text = (x, y, s, cls, anchor = "middle") => `<text x="${x}" y="${y}" class="${cls}" text-anchor="${anchor}">${esc(s)}</text>`;

function drawLeaf(n, b) {
  const t = leafText(n);
  const cx = b.x + b.width / 2;
  const onClass = n.kind === "component" ? " on-component" : "";
  const parts = [];
  if (n.kind === "db" || n.kind === "ext-db") {
    const ry = 9, { x, y, width: w, height: h } = b;
    parts.push(`<path class="shape ${n.kind}" d="M${x} ${y + ry} a${w / 2} ${ry} 0 0 1 ${w} 0 v${h - 2 * ry} a${w / 2} ${ry} 0 0 1 -${w} 0 z"/>`);
    parts.push(`<path class="shape-line" d="M${x} ${y + ry} a${w / 2} ${ry} 0 0 0 ${w} 0"/>`);
  } else {
    parts.push(`<rect class="shape ${n.kind}" x="${b.x}" y="${b.y}" width="${b.width}" height="${b.height}" rx="8"/>`);
  }
  let y = b.y + 14 + (n.kind === "db" || n.kind === "ext-db" ? 8 : 0) + 11;
  for (const l of t.name) { parts.push(text(cx, y, l, "node-name" + onClass)); y += LINE.name; }
  y -= 3;
  for (const l of t.tech) { parts.push(text(cx, y, l, "node-tech" + onClass)); y += LINE.tech; }
  if (t.desc.length) y += 4;
  for (const l of t.desc) { parts.push(text(cx, y, l, "node-desc" + onClass)); y += LINE.desc; }
  return parts.join("");
}

function drawGroup(n, b) {
  const cls = n.kind === "boundary" ? "boundary" : "deploy";
  const tech = n.tech ? `[${n.tech}]` : "";
  return `<rect class="${cls}" x="${b.x}" y="${b.y}" width="${b.width}" height="${b.height}" rx="10"/>`
    + text(b.x + 14, b.y + 22, n.name, "group-name", "start")
    + (tech ? text(b.x + 14, b.y + 37, tech, "group-tech", "start") : "");
}

function rounded(points, r = 7) {
  let d = `M${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const p = points[i], a = points[i - 1], c = points[i + 1];
    const l1 = Math.hypot(p.x - a.x, p.y - a.y), l2 = Math.hypot(c.x - p.x, c.y - p.y);
    const k1 = Math.min(r, l1 / 2) / l1, k2 = Math.min(r, l2 / 2) / l2;
    d += ` L${p.x + (a.x - p.x) * k1} ${p.y + (a.y - p.y) * k1} Q${p.x} ${p.y} ${p.x + (c.x - p.x) * k2} ${p.y + (c.y - p.y) * k2}`;
  }
  const last = points[points.length - 1];
  return d + ` L${last.x} ${last.y}`;
}

const STYLE = `
  svg { --bg:#ffffff; --fg:#1b2a33; --muted:#5b6b75; --line:#6b7b86; --group:#8b98a1; --deploy-fill:#f4f7f9;
        --person:#08427b; --system:#1168bd; --container:#438dd5; --component:#85bbf0; --ext:#8c9399;
        --on-dark:#ffffff; --on-component:#0b2540; font-family: "Segoe UI", system-ui, -apple-system, Helvetica, Arial, sans-serif; }
  @media (prefers-color-scheme: dark) { svg { --bg:#0f1720; --fg:#e3ebf1; --muted:#9fb0bc; --line:#8fa1ad; --group:#4b5b66; --deploy-fill:#16212b;
        --person:#0b4f94; --system:#1a73c9; --container:#2f6aa3; --component:#2b5d8f; --ext:#59626a; --on-component:#ffffff; } }
  .bg { fill: var(--bg); }
  .title { fill: var(--fg); font-size: 17px; font-weight: 700; }
  .subtitle { fill: var(--muted); font-size: 12px; }
  .shape { stroke: rgba(0,0,0,0.25); stroke-width: 1; }
  .shape-line { fill: none; stroke: rgba(0,0,0,0.25); stroke-width: 1; }
  .person { fill: var(--person); } .system { fill: var(--system); }
  .container, .db { fill: var(--container); } .component { fill: var(--component); }
  .ext, .ext-container, .ext-db { fill: var(--ext); }
  .node-name { fill: var(--on-dark); font-size: 14px; font-weight: 700; }
  .node-tech { fill: var(--on-dark); font-size: 11px; font-style: italic; opacity: 0.92; }
  .node-desc { fill: var(--on-dark); font-size: 12px; }
  .node-name.on-component, .node-tech.on-component, .node-desc.on-component { fill: var(--on-component); }
  .boundary { fill: none; stroke: var(--group); stroke-width: 1.5; stroke-dasharray: 7 5; }
  .deploy { fill: var(--deploy-fill); stroke: var(--group); stroke-width: 1.5; }
  .group-name { fill: var(--fg); font-size: 14px; font-weight: 700; }
  .group-tech { fill: var(--muted); font-size: 11px; }
  .edge { fill: none; stroke: var(--line); stroke-width: 1.4; }
  .arrow { fill: var(--line); }
  .elabel-bg { fill: var(--bg); opacity: 0.92; }
  .elabel { fill: var(--fg); font-size: 11.5px; }
  .elabel-tech { fill: var(--muted); font-size: 10.5px; font-style: italic; }
  .legend-t { fill: var(--muted); font-size: 11.5px; }
  .note { fill: var(--muted); font-size: 11.5px; }
`;

async function drawView(view) {
  const index = new Map();
  const labelsByEdge = view.edges.map(([, , label, tech]) => edgeLabel(label, tech));
  const graph = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": view.dir ?? "DOWN",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
      "elk.spacing.nodeNode": "34",
      "elk.layered.spacing.nodeNodeBetweenLayers": "70",
      "elk.layered.spacing.edgeNodeBetweenLayers": "26",
      "elk.layered.spacing.edgeEdgeBetweenLayers": "14",
      "elk.spacing.edgeNode": "24",
      "elk.spacing.edgeEdge": "14",
      "elk.spacing.edgeLabel": "4",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.json.edgeCoords": "ROOT",
      "elk.json.shapeCoords": "ROOT",
      "elk.padding": "[top=70,left=30,bottom=30,right=30]",
    },
    children: view.nodes.map((n) => toElk(n, index)),
    edges: view.edges.map(([from, to], i) => ({
      id: `e${i}`, sources: [from], targets: [to],
      labels: [{ id: `l${i}`, text: "x", width: labelsByEdge[i].width, height: labelsByEdge[i].height }],
    })),
  };
  const laid = await elk.layout(graph);

  const body = [];
  const walk = (children, draw) => children.forEach((c) => { draw(c); if (c.children) walk(c.children, draw); });
  // Groups first (back to front), then edges, then boxes, so a line never hides a box's text.
  walk(laid.children, (c) => { const n = index.get(c.id); if (!LEAF.has(n.kind)) body.push(drawGroup(n, c)); });
  laid.edges.forEach((e, i) => {
    const s = e.sections[0];
    const pts = [s.startPoint, ...(s.bendPoints ?? []), s.endPoint];
    body.push(`<path class="edge" d="${rounded(pts)}" marker-end="url(#arrow)"/>`);
  });
  walk(laid.children, (c) => { const n = index.get(c.id); if (LEAF.has(n.kind)) body.push(drawLeaf(n, c)); });
  laid.edges.forEach((e, i) => {
    const l = e.labels[0], info = labelsByEdge[i];
    body.push(`<rect class="elabel-bg" x="${l.x}" y="${l.y}" width="${l.width}" height="${l.height}" rx="3"/>`);
    let y = l.y + 13;
    info.lines.forEach((line, k) => {
      const isTech = view.edges[i][3] && k >= info.lines.length - wrap(`[${view.edges[i][3]}]`, 30).length;
      body.push(text(l.x + l.width / 2, y, line, isTech ? "elabel-tech" : "elabel"));
      y += LINE.edge;
    });
  });

  // Legend: only the kinds this view uses.
  const used = new Set();
  walk(laid.children, (c) => used.add(index.get(c.id).kind));
  const legend = [
    ["person", "Person"], ["system", "This system"], ["container", "Container"], ["component", "Component"],
    ["db", "Data store"], ["ext", "Outside the system"], ["boundary", "Boundary"], ["deploy", "Deployment node"],
  ].filter(([k]) => used.has(k) || (k === "ext" && (used.has("ext-container") || used.has("ext-db"))));
  const noteLines = view.note ? wrap(view.note, Math.floor((laid.width - 40) / 6.1)) : [];
  const legendY = laid.height + 22;
  let lx = 30;
  const legendSvg = legend.map(([k, label]) => {
    const swatch = k === "boundary" ? `<rect class="boundary" x="${lx}" y="${legendY - 10}" width="22" height="13" rx="3"/>`
      : k === "deploy" ? `<rect class="deploy" x="${lx}" y="${legendY - 10}" width="22" height="13" rx="3"/>`
      : `<rect class="shape ${k}" x="${lx}" y="${legendY - 10}" width="22" height="13" rx="3"/>`;
    const out = swatch + text(lx + 29, legendY, label, "legend-t", "start");
    lx += 29 + label.length * 6.3 + 22;
    return out;
  }).join("");
  const noteSvg = noteLines.map((l, i) => text(30, legendY + 26 + i * 15, l, "note", "start")).join("");

  const width = Math.max(laid.width + 0, lx + 10);
  const height = legendY + 26 + noteLines.length * 15 + 16;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${Math.ceil(width)} ${Math.ceil(height)}" width="${Math.ceil(width)}" height="${Math.ceil(height)}" role="img" aria-label="${esc(view.title)}">
<style>${STYLE}</style>
<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path class="arrow" d="M0 0 L10 5 L0 10 z"/></marker></defs>
<rect class="bg" width="100%" height="100%"/>
${text(30, 34, view.title, "title", "start")}
${body.join("\n")}
${legendSvg}
${noteSvg}
</svg>
`;
  const file = join(outDir, `${view.id}.svg`);
  writeFileSync(file, svg);
  console.log(`${view.id}.svg  ${Math.ceil(width)} x ${Math.ceil(height)}`);
}

const only = process.argv[2];
for (const v of views) if (!only || v.id === only) await drawView(v);
