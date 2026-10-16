import { describe, expect, it } from "vitest";
import { imageSource, parseInline, parseMarkdown, safeHref } from "./markdown";

describe("links", () => {
  it("allows web, mail and anchors only", () => {
    expect(safeHref("https://a.example/x")).toBe("https://a.example/x");
    expect(safeHref("mailto:a@b.c")).toBe("mailto:a@b.c");
    expect(safeHref("#top")).toBe("#top");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("  JaVaScRiPt:alert(1)")).toBeNull();
    expect(safeHref("data:text/html,<script>")).toBeNull();
    expect(safeHref("file:///etc/passwd")).toBeNull();
  });

  it("drops an unsafe link to its text", () => {
    expect(parseInline("[hi](javascript:alert(1))")).toEqual([{ t: "text", text: "hi" }]);
  });
});

describe("images", () => {
  it("resolves a relative path against the file's folder", () => {
    expect(imageSource("chart.png", "reports/a.md")).toEqual({ kind: "artifact", path: "reports/chart.png" });
    expect(imageSource("../x.png", "reports/a.md")).toEqual({ kind: "artifact", path: "x.png" });
  });
  it("refuses to climb out of the folder, and refuses remote or absolute sources", () => {
    expect(imageSource("../x.png", "a.md")).toBeNull();
    expect(imageSource("http://evil.example/p.png", "a.md")).toBeNull();
    expect(imageSource("//evil.example/p.png", "a.md")).toBeNull();
    expect(imageSource("/etc/passwd", "a.md")).toBeNull();
    expect(imageSource("data:image/svg+xml;base64,PHN2Zz4=", "a.md")).toBeNull();
  });
  it("allows a base64 raster data image", () => {
    expect(imageSource("data:image/png;base64,AAAA", "a.md")).toEqual({ kind: "data", url: "data:image/png;base64,AAAA" });
  });
  it("shows alt text for a remote image", () => {
    expect(parseInline("![a chart](https://evil.example/p.png)")).toEqual([{ t: "text", text: "a chart" }]);
  });
});

describe("raw HTML", () => {
  it("stays plain text", () => {
    expect(parseMarkdown("<script>alert(1)</script>")).toEqual([
      { t: "paragraph", children: [{ t: "text", text: "<script>alert(1)</script>" }] },
    ]);
  });
});

describe("blocks", () => {
  it("reads headings, paragraphs and emphasis", () => {
    const tree = parseMarkdown("# Title\n\nSome **bold** and *it* and `code`.");
    expect(tree[0]).toMatchObject({ t: "heading", level: 1 });
    expect(tree[1]).toMatchObject({ t: "paragraph" });
    const kinds = (tree[1] as { children: { t: string }[] }).children.map((c) => c.t);
    expect(kinds).toEqual(["text", "strong", "text", "em", "text", "code", "text"]);
  });
  it("reads a fenced block verbatim", () => {
    expect(parseMarkdown("```py\nx = '**no**'\n```")).toEqual([{ t: "code", lang: "py", text: "x = '**no**'" }]);
  });
  it("reads nested lists", () => {
    const [list] = parseMarkdown("- a\n  - b\n- c") as { t: "list"; items: unknown[][] }[];
    expect(list.items).toHaveLength(2);
    expect((list.items[0] as { t: string }[]).map((b) => b.t)).toEqual(["paragraph", "list"]);
  });
  it("reads numbered lists", () => {
    expect(parseMarkdown("1. a\n2. b")[0]).toMatchObject({ t: "list", ordered: true });
  });
  it("reads a table", () => {
    const [table] = parseMarkdown("| a | b |\n|---|---|\n| 1 | 2 |") as { t: "table"; head: unknown[]; rows: unknown[] }[];
    expect(table.t).toBe("table");
    expect(table.head).toHaveLength(2);
    expect(table.rows).toHaveLength(1);
  });
  it("reads a quote and a rule", () => {
    expect(parseMarkdown("> hi\n\n---")).toMatchObject([{ t: "quote" }, { t: "rule" }]);
  });
  it("does not hang on an unclosed fence or odd input", () => {
    expect(parseMarkdown("```\nnever closed")).toEqual([{ t: "code", lang: "", text: "never closed" }]);
    expect(() => parseMarkdown("| \n|\n- \n> \n#")).not.toThrow();
  });
});
