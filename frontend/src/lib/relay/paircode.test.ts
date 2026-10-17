import { describe, expect, it } from "vitest";
import { formatCode, shortCode } from "./paircode";

describe("shortCode", () => {
  it("matches the daemon's for the same handshake hash (protocol/pairing.py pins the same two)", async () => {
    expect(await shortCode(Uint8Array.from({ length: 32 }, (_, i) => i))).toBe("497170");
    expect(await shortCode(new Uint8Array(32))).toBe("864793");
  });

  it("is six digits, with leading zeros kept, and differs between handshakes", async () => {
    const seen = new Set<string>();
    for (let i = 0; i < 40; i++) {
      const code = await shortCode(new Uint8Array(32).fill(i));
      expect(code).toMatch(/^\d{6}$/);
      seen.add(code);
    }
    expect(seen.size).toBeGreaterThan(35);
  });

  it("is shown as two groups of three", () => {
    expect(formatCode("497170")).toBe("497 170");
  });
});
