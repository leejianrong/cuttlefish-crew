import { describe, expect, it } from "vitest";
import { describeFlow, layoutFlow, parseFlow } from "./flow";

describe("mermaid flowchart subset", () => {
  it("reads nodes, shapes, chains and labels", () => {
    const spec = parseFlow('flowchart LR\n  A[Start] --> B{Ok?}\n  B -->|yes| C(Ship)\n  B -.->|no| D["Fix it"]; D --> A');
    expect(spec?.down).toBe(false);
    expect(spec?.nodes.map((n) => [n.id, n.text, n.shape])).toEqual([
      ["A", "Start", "box"],
      ["B", "Ok?", "diamond"],
      ["C", "Ship", "round"],
      ["D", "Fix it", "box"],
    ]);
    expect(spec?.edges.map((e) => [e.from, e.to, e.label, e.style])).toEqual([
      ["A", "B", "", "solid"],
      ["B", "C", "yes", "solid"],
      ["B", "D", "no", "dotted"],
      ["D", "A", "", "solid"],
    ]);
  });
  it("lets a later mention name an earlier bare id, and skips comments", () => {
    const spec = parseFlow("graph TD\n%% note\nA --> B\nB[Done]");
    expect(spec?.down).toBe(true);
    expect(spec?.nodes.find((n) => n.id === "B")?.text).toBe("Done");
    expect(spec?.nodes.find((n) => n.id === "A")?.text).toBe("A");
  });
  it("refuses what it does not draw", () => {
    for (const bad of ["", "pie\n  a: 1", "graph BT\nA-->B", "graph TD\nsubgraph s\nA-->B\nend", "graph TD\nA-->", "graph TD\nstyle A fill:#f00", "graph TD\nA([x])-->B"]) {
      expect(parseFlow(bad), bad).toBeNull();
    }
    expect(parseFlow("graph TD\n" + Array.from({ length: 41 }, (_, i) => `N${i}`).join("\n"))).toBeNull();
  });
  it("lays out by rank, survives a cycle and a self loop, and keeps every node in the box", () => {
    const spec = parseFlow("graph TD\nA-->B\nB-->C\nC-->A\nC-->C\nA-->D");
    expect(spec).not.toBeNull();
    const l = layoutFlow(spec!);
    const y = (id: string) => l.nodes.find((n) => n.id === id)!.y;
    expect(y("A")).toBeLessThan(y("B"));
    expect(y("B")).toBeLessThan(y("C"));
    expect(l.edges).toHaveLength(4);
    for (const n of l.nodes) {
      expect(n.x - n.w / 2).toBeGreaterThanOrEqual(0);
      expect(n.x + n.w / 2).toBeLessThanOrEqual(l.width);
      expect(n.y + n.h / 2).toBeLessThanOrEqual(l.height);
    }
  });
  it("runs left to right for LR and keeps node text as text", () => {
    const spec = parseFlow("graph LR\nA[<b>x</b>]-->B");
    expect(spec?.nodes[0].text).toBe("<b>x</b>");
    const l = layoutFlow(spec!);
    expect(l.nodes[0].x).toBeLessThan(l.nodes[1].x);
    expect(describeFlow(spec!)).toContain("to B");
  });
});
