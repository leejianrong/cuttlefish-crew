import { describe, expect, it } from "vitest";
import { describeChart, layoutChart, niceStep, parseChartSpec } from "./chart";
import { parseMarkdown } from "./markdown";

const fence = (lang: string, body: string) => "```" + lang + "\n" + body + "\n```";

describe("chart spec", () => {
  it("reads a bar chart with series, and the one-series shorthand", () => {
    const spec = parseChartSpec('{"labels":["a","b"],"series":[{"name":"x","values":[1,2]},{"name":"y","values":[3,4]}]}');
    expect(spec?.kind).toBe("bar");
    expect(spec?.series).toHaveLength(2);
    expect(parseChartSpec('{"type":"line","labels":[1,2],"values":[5,6]}')?.series[0].values).toEqual([5, 6]);
  });
  it("refuses anything unusable", () => {
    for (const bad of [
      "nope",
      "[]",
      '{"labels":[],"values":[]}',
      '{"labels":["a"],"values":[1,2]}',
      '{"labels":["a"],"values":["1"]}',
      '{"labels":["a"],"values":[1e999]}',
      '{"type":"pie","labels":["a"],"values":[1]}',
      '{"labels":[{"x":1}],"values":[1]}',
      '{"labels":["a"]}',
    ]) {
      expect(parseChartSpec(bad), bad).toBeNull();
    }
  });
  it("caps labels and series", () => {
    const labels = Array.from({ length: 61 }, (_, i) => i);
    expect(parseChartSpec(JSON.stringify({ labels, values: labels }))).toBeNull();
    const series = Array.from({ length: 7 }, () => ({ values: [1] }));
    expect(parseChartSpec(JSON.stringify({ labels: ["a"], series }))).toBeNull();
  });
  it("picks round steps", () => {
    expect(niceStep(0.7)).toBe(1);
    expect(niceStep(3)).toBe(5);
    expect(niceStep(12)).toBe(20);
  });
  it("lays bars out inside the plot, above and below zero", () => {
    const spec = parseChartSpec('{"labels":["a","b"],"values":[10,-5]}');
    if (!spec) throw new Error("spec");
    const layout = layoutChart(spec);
    const [up, down] = layout.bars;
    expect(up.y + up.h).toBeCloseTo(layout.zeroY);
    expect(down.y).toBeCloseTo(layout.zeroY);
    for (const bar of layout.bars) {
      expect(bar.y).toBeGreaterThanOrEqual(layout.plot.top - 0.01);
      expect(bar.y + bar.h).toBeLessThanOrEqual(layout.plot.bottom + 0.01);
    }
    expect(layout.ticks.map((t) => t.value)).toContain(0);
  });
  it("copes with all zeros and draws a line per series", () => {
    const flat = parseChartSpec('{"type":"line","labels":["a","b"],"values":[0,0]}');
    if (!flat) throw new Error("spec");
    const layout = layoutChart(flat);
    expect(layout.lines[0].points.every((p) => Number.isFinite(p.y))).toBe(true);
    expect(describeChart(flat)).toContain("a 0, b 0");
  });
});

describe("fenced blocks", () => {
  it("draws a chart and keeps a malformed one as code", () => {
    expect(parseMarkdown(fence("chart", '{"labels":["a"],"values":[1]}'))[0].t).toBe("chart");
    expect(parseMarkdown(fence("chart", "{oops"))[0]).toEqual({ t: "code", lang: "chart", text: "{oops" });
  });
  it("reads a csv table, with quotes, and pads short rows", () => {
    const [table] = parseMarkdown(fence("table", 'name,note\nfoo,"a, b"\nbar'));
    expect(table.t).toBe("table");
    if (table.t !== "table") return;
    expect(table.head).toHaveLength(2);
    expect(table.rows[0][1]).toEqual([{ t: "text", text: "a, b" }]);
    expect(table.rows[1][1]).toEqual([]);
  });
  it("leaves a one-column or header-only table as code", () => {
    expect(parseMarkdown(fence("table", "a\nb"))[0].t).toBe("code");
    expect(parseMarkdown(fence("table", "a,b"))[0].t).toBe("code");
  });
  it("marks diff lines", () => {
    const [diff] = parseMarkdown(fence("diff", "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n same"));
    expect(diff.t).toBe("diff");
    if (diff.t !== "diff") return;
    expect(diff.lines.map((l) => l.kind)).toEqual(["hunk", "hunk", "hunk", "del", "add", "ctx"]);
  });
  it("leaves a diff with no changes as code", () => {
    expect(parseMarkdown(fence("diff", "just text"))[0].t).toBe("code");
  });
  it("reads a checklist and refuses a line that is not an item", () => {
    const [list] = parseMarkdown(fence("checklist", "- [x] done **bold**\n[ ] todo"));
    expect(list.t).toBe("checklist");
    if (list.t !== "checklist") return;
    expect(list.items.map((i) => i.done)).toEqual([true, false]);
    expect(parseMarkdown(fence("checklist", "- [x] a\nplain"))[0].t).toBe("code");
  });
  it("never produces markup from block text", () => {
    const [diff] = parseMarkdown(fence("diff", "+<script>alert(1)</script>"));
    expect(diff).toEqual({ t: "diff", lines: [{ kind: "add", text: "+<script>alert(1)</script>" }] });
  });
  it("leaves other languages, and mermaid for now, as code", () => {
    expect(parseMarkdown(fence("python", "print(1)"))[0].t).toBe("code");
    expect(parseMarkdown(fence("mermaid", "graph TD; A-->B"))[0].t).toBe("code");
  });
});
