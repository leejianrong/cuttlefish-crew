import { describe, expect, it } from "vitest";
import { PairingError, decodePairing, fromBase64Url, toBase64Url } from "./pairing";

function payload(body: unknown): string {
  return toBase64Url(new TextEncoder().encode(JSON.stringify(body)));
}
const key = (fill: number) => toBase64Url(new Uint8Array(32).fill(fill));
const good = { v: 1, relay: "ws://127.0.0.1:8450/", room: "abc12345", daemon: key(1), psk: key(2) };

describe("base64url", () => {
  it("round-trips every byte value", () => {
    const all = new Uint8Array(256).map((_, i) => i);
    expect(fromBase64Url(toBase64Url(all))).toEqual(all);
    expect(toBase64Url(all)).not.toMatch(/[+/=]/);
  });
});

describe("decodePairing", () => {
  it("reads a good payload and drops a trailing slash from the relay address", () => {
    const config = decodePairing(payload(good));
    expect(config.relay).toBe("ws://127.0.0.1:8450");
    expect(config.room).toBe("abc12345");
    expect(config.daemon).toEqual(new Uint8Array(32).fill(1));
    expect(config.psk).toEqual(new Uint8Array(32).fill(2));
  });

  it("says what is wrong in words for a person", () => {
    const cases: [string, unknown][] = [
      ["damaged", "%%%not base64%%%"],
      ["understands", { ...good, v: 2 }],
      ["websocket address", { ...good, relay: "http://x" }],
      ["missing a key", { ...good, psk: undefined }],
      ["wrong size", { ...good, psk: key(3).slice(0, 10) }],
    ];
    for (const [needle, body] of cases) {
      const text = typeof body === "string" ? body : payload(body);
      expect(() => decodePairing(text)).toThrow(PairingError);
      expect(() => decodePairing(text)).toThrow(new RegExp(needle));
    }
  });
});
