// A small markdown reader for what an agent writes to `.cuttlefish/artifacts/` (CUT-97).
//
// It produces a tree, never HTML: the page renders the tree with Svelte, so no string of
// markup is ever injected and a raw `<script>` in the file stays plain text. That is the whole
// security argument, so keep it that way: do not add an "allow some HTML" path here.
//
// Supported: headings, paragraphs, bold, italic, inline code, fenced code, block quotes,
// bullet and numbered lists (nested by indent), tables, rules, links and images. Anything else
// is shown as the text it is.
//
// Five fenced languages are drawn rather than shown as code (CUT-114, CUT-278): `chart` (a small JSON
// spec), `table` (CSV), `diff`, `checklist` and `mermaid` (a flowchart subset, see flow.ts). Each is parsed into a tree like everything else;
// a block that does not parse stays a plain code block, so a bad one never breaks the page.

import { parseChartSpec, type ChartSpec } from "./chart";
import { parseFlow, type FlowSpec } from "./flow";

export type Inline =
  | { t: "text"; text: string }
  | { t: "code"; text: string }
  | { t: "strong"; children: Inline[] }
  | { t: "em"; children: Inline[] }
  | { t: "link"; href: string; children: Inline[] }
  | { t: "image"; src: ImageSource; alt: string }
  | { t: "br" };

/** Where an image comes from. Anything else is dropped to its alt text. */
export type ImageSource =
  | { kind: "data"; url: string }
  | { kind: "artifact"; path: string };

export type Block =
  | { t: "heading"; level: number; children: Inline[] }
  | { t: "paragraph"; children: Inline[] }
  | { t: "code"; lang: string; text: string }
  | { t: "quote"; children: Block[] }
  | { t: "list"; ordered: boolean; items: Block[][] }
  | { t: "table"; head: Inline[][]; rows: Inline[][][] }
  | { t: "rule" }
  | { t: "chart"; spec: ChartSpec }
  | { t: "flow"; spec: FlowSpec }
  | { t: "diff"; lines: { kind: "add" | "del" | "hunk" | "ctx"; text: string }[] }
  | { t: "checklist"; items: { done: boolean; text: Inline[] }[] };

const MAX_BLOCK_LINES = 500;

/** CSV as rows: commas, double-quoted cells with `""` for a quote, and no multi-line cells. */
function csvRow(line: string): string[] {
  const out: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') {
        cell += '"';
        i += 1;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") {
      out.push(cell.trim());
      cell = "";
    } else cell += ch;
  }
  out.push(cell.trim());
  return out;
}

/** What a fenced block of `lang` draws as, or `null` to leave it as code. */
export function richBlock(lang: string, text: string, fromPath = ""): Block | null {
  const name = lang.toLowerCase();
  const lines = text.split("\n");
  if (name === "chart") {
    const spec = parseChartSpec(text);
    return spec ? { t: "chart", spec } : null;
  }
  if (name === "mermaid") {
    const spec = parseFlow(text);
    return spec ? { t: "flow", spec } : null;
  }
  if (name === "table") {
    const rows = lines.filter((l) => l.trim() !== "").map(csvRow);
    if (rows.length < 2 || rows.length > MAX_BLOCK_LINES) return null;
    const width = rows[0].length;
    if (width < 2 || width > 20) return null;
    const fit = (row: string[]) => Array.from({ length: width }, (_, k) => parseInline(row[k] ?? "", fromPath));
    return { t: "table", head: fit(rows[0]), rows: rows.slice(1).map(fit) };
  }
  if (name === "diff" || name === "patch") {
    if (lines.length > MAX_BLOCK_LINES || !lines.some((l) => /^[+-]/.test(l))) return null;
    return {
      t: "diff",
      lines: lines.map((l) => ({
        kind: l.startsWith("@@") ? "hunk" : /^(\+\+\+|---) /.test(l) ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : "ctx",
        text: l,
      })),
    };
  }
  if (name === "checklist") {
    const items: { done: boolean; text: Inline[] }[] = [];
    for (const l of lines) {
      if (l.trim() === "") continue;
      const m = /^\s*(?:[-*+]\s+)?\[([ xX])\]\s+(.*)$/.exec(l);
      if (!m) return null;
      items.push({ done: m[1] !== " ", text: parseInline(m[2], fromPath) });
    }
    return items.length > 0 && items.length <= MAX_BLOCK_LINES ? { t: "checklist", items } : null;
  }
  return null;
}

const SAFE_DATA_IMAGE = /^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+$/;

/** A link target a person may follow: web pages, mail and in-page anchors only. */
export function safeHref(href: string): string | null {
  const value = href.trim();
  if (/^(https?:|mailto:)/i.test(value) || value.startsWith("#")) return value;
  return null;
}

/** `src` as an image source, resolved against the folder of the file that holds it. */
export function imageSource(src: string, fromPath: string): ImageSource | null {
  const value = src.trim();
  if (SAFE_DATA_IMAGE.test(value)) return { kind: "data", url: value };
  if (/^[a-z][a-z0-9+.-]*:/i.test(value) || value.startsWith("//") || value.startsWith("/")) {
    return null;
  }
  const parts = fromPath.split("/").slice(0, -1);
  for (const part of value.split(/[?#]/)[0].split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") {
      if (parts.length === 0) return null;
      parts.pop();
    } else parts.push(part);
  }
  return parts.length ? { kind: "artifact", path: parts.join("/") } : null;
}

const SPECIAL = /[`*_[!\\\n]/;

/** The index of the `)` that closes a link target starting at `from`, allowing nested pairs. */
function closingParen(source: string, from: number): number {
  let depth = 1;
  for (let j = from; j < source.length; j += 1) {
    if (source[j] === "(") depth += 1;
    else if (source[j] === ")" && (depth -= 1) === 0) return j;
    else if (source[j] === "\n") return -1;
  }
  return -1;
}

export function parseInline(source: string, fromPath = ""): Inline[] {
  const out: Inline[] = [];
  let text = "";
  const flush = () => {
    if (text) out.push({ t: "text", text });
    text = "";
  };
  let i = 0;
  while (i < source.length) {
    const ch = source[i];
    if (!SPECIAL.test(ch)) {
      text += ch;
      i += 1;
      continue;
    }
    if (ch === "\\" && i + 1 < source.length) {
      text += source[i + 1];
      i += 2;
      continue;
    }
    if (ch === "\n") {
      flush();
      out.push({ t: "br" });
      i += 1;
      continue;
    }
    if (ch === "`") {
      const close = source.indexOf("`", i + 1);
      if (close > i) {
        flush();
        out.push({ t: "code", text: source.slice(i + 1, close) });
        i = close + 1;
        continue;
      }
    }
    if ((ch === "*" || ch === "_") && source[i + 1] === ch) {
      const close = source.indexOf(ch + ch, i + 2);
      if (close > i + 2) {
        flush();
        out.push({ t: "strong", children: parseInline(source.slice(i + 2, close), fromPath) });
        i = close + 2;
        continue;
      }
    }
    if (ch === "*" || ch === "_") {
      const close = source.indexOf(ch, i + 1);
      const intraword = ch === "_" && /\w/.test(source[i - 1] ?? "");
      if (close > i + 1 && !intraword && !/\s/.test(source[i + 1])) {
        flush();
        out.push({ t: "em", children: parseInline(source.slice(i + 1, close), fromPath) });
        i = close + 1;
        continue;
      }
    }
    if (ch === "[" || (ch === "!" && source[i + 1] === "[")) {
      const image = ch === "!";
      const start = image ? i + 1 : i;
      const mid = source.indexOf("](", start);
      const end = mid > start ? closingParen(source, mid + 2) : -1;
      if (mid > start && end > mid) {
        const label = source.slice(start + 1, mid);
        const target = source.slice(mid + 2, end).trim().split(/\s+/)[0] ?? "";
        flush();
        if (image) {
          const src = imageSource(target, fromPath);
          if (src) out.push({ t: "image", src, alt: label });
          else if (label) out.push({ t: "text", text: label });
        } else {
          const href = safeHref(target);
          const children = parseInline(label, fromPath);
          if (href) out.push({ t: "link", href, children });
          else out.push(...children);
        }
        i = end + 1;
        continue;
      }
    }
    text += ch;
    i += 1;
  }
  flush();
  return out;
}

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([\w+-]*)/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^ {0,3}([-*_])(\s*\1){2,}\s*$/;
const ITEM = /^( *)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const TABLE_RULE = /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/;

function cells(line: string): string[] {
  let value = line.trim();
  if (value.startsWith("|")) value = value.slice(1);
  if (value.endsWith("|") && !value.endsWith("\\|")) value = value.slice(0, -1);
  return value.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
}

export function parseMarkdown(source: string, fromPath = ""): Block[] {
  return blocks(source.replace(/\r\n?/g, "\n").split("\n"), fromPath);
}

function blocks(lines: string[], fromPath: string): Block[] {
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "") {
      i += 1;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[1];
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith(marker)) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1;
      const text = body.join("\n");
      out.push(richBlock(fence[2], text, fromPath) ?? { t: "code", lang: fence[2], text });
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      out.push({ t: "heading", level: heading[1].length, children: parseInline(heading[2], fromPath) });
      i += 1;
      continue;
    }
    if (RULE.test(line)) {
      out.push({ t: "rule" });
      i += 1;
      continue;
    }
    if (/^ {0,3}>/.test(line)) {
      const inner: string[] = [];
      while (i < lines.length && /^ {0,3}>/.test(lines[i])) {
        inner.push(lines[i].replace(/^ {0,3}> ?/, ""));
        i += 1;
      }
      out.push({ t: "quote", children: blocks(inner, fromPath) });
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const head = cells(line).map((c) => parseInline(c, fromPath));
      i += 2;
      const rows: Inline[][][] = [];
      while (i < lines.length && lines[i].trim() !== "" && lines[i].includes("|")) {
        rows.push(cells(lines[i]).map((c) => parseInline(c, fromPath)));
        i += 1;
      }
      out.push({ t: "table", head, rows });
      continue;
    }
    const item = ITEM.exec(line);
    if (item) {
      const base = item[1].length;
      const ordered = /\d/.test(item[2][0]);
      const items: Block[][] = [];
      while (i < lines.length) {
        const m = ITEM.exec(lines[i]);
        if (!m || m[1].length !== base || /\d/.test(m[2][0]) !== ordered) break;
        const body = [m[3]];
        i += 1;
        while (i < lines.length && lines[i].trim() !== "") {
          const next = ITEM.exec(lines[i]);
          if (next && next[1].length <= base) break;
          if (!next && !/^\s/.test(lines[i])) break;
          body.push(lines[i].slice(Math.min(lines[i].length - lines[i].trimStart().length, base + 2)));
          i += 1;
        }
        items.push(blocks(body, fromPath));
        while (i < lines.length && lines[i].trim() === "" && ITEM.test(lines[i + 1] ?? "")) i += 1;
      }
      out.push({ t: "list", ordered, items });
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() !== "" && !FENCE.test(lines[i]) && !HEADING.test(lines[i]) && !/^ {0,3}>/.test(lines[i]) && !RULE.test(lines[i]) && !(para.length && ITEM.test(lines[i]))) {
      para.push(lines[i].trim());
      i += 1;
    }
    if (para.length === 0) {
      para.push(line.trim());
      i += 1;
    }
    out.push({ t: "paragraph", children: parseInline(para.join("\n"), fromPath) });
  }
  return out;
}
