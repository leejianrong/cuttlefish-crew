// Unit: lib/relay imports nothing from the rest of the app (ADR-0037). Like the Python protocol package, it must be
// movable to another repository without untangling. Allowed: its own files and @noble/curves (tests may also use
// vitest and the shared vectors file).

import { describe, expect, it } from "vitest";

// Every source file next to this one, read as text. This file holds sample import lines as strings, so the scan
// leaves it out.
const sources = Object.entries(
  import.meta.glob<string>("./*.ts", { query: "?raw", import: "default", eager: true }),
)
  .map(([path, source]) => [path.replace("./", ""), source] as const)
  .filter(([file]) => file !== "boundary.test.ts");

function imports(source: string): string[] {
  return [...source.matchAll(/(?:import|export)\s[^;]*?from\s+["']([^"']+)["']/g)].map((m) => m[1]);
}

describe("lib/relay boundary", () => {
  it("has files to check", () => {
    expect(sources.length).toBeGreaterThanOrEqual(5);
  });

  it("imports only its own files, @noble/curves and, in tests, vitest and node", () => {
    const offenders: string[] = [];
    for (const [file, source] of sources) {
      const isTest = file.endsWith(".test.ts") || file === "vectors.ts";
      for (const spec of imports(source)) {
        const ok =
          spec.startsWith("./") ||
          spec.startsWith("@noble/curves") ||
          // The tests' shared vectors file lives with the other protocol tests, outside the app.
          (isTest && (spec === "vitest" || spec.endsWith("/vectors.json")));
        if (!ok) offenders.push(`${file}: ${spec}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("the rule would catch a violation", () => {
    const sample = ["import { x } from ", '"../api";\n', "export * from ", '"./frames";'].join("");
    expect(imports(sample)).toEqual(["../api", "./frames"]);
  });
});
