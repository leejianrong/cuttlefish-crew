import { describe, expect, it } from "vitest";
import { isSecureAddress, pairingAddressProblem, webCryptoAvailable } from "./secure";

describe("webCryptoAvailable", () => {
  it("needs a secure context and crypto.subtle", () => {
    expect(webCryptoAvailable({ isSecureContext: true, crypto: { subtle: {} } })).toBe(true);
    expect(webCryptoAvailable({ isSecureContext: false, crypto: { subtle: {} } })).toBe(false);
    // What a page at http://100.x.y.z shows: subtle is simply not there.
    expect(webCryptoAvailable({ isSecureContext: false, crypto: {} })).toBe(false);
    expect(webCryptoAvailable({ crypto: {} })).toBe(false);
    expect(webCryptoAvailable({})).toBe(false);
  });
});

describe("isSecureAddress", () => {
  it("is https, or http on this machine", () => {
    for (const ok of ["https://x.ts.net", "http://localhost:8420", "http://127.0.0.1:8420", "http://[::1]:8420"]) {
      expect(isSecureAddress(ok), ok).toBe(true);
    }
    for (const bad of ["http://100.65.64.86:8431", "http://192.168.1.5", "http://ideapadj.tail917f06.ts.net:8431", "not a url", ""]) {
      expect(isSecureAddress(bad), bad).toBe(false);
    }
  });
});

describe("pairingAddressProblem", () => {
  it("says nothing about an https address and explains each address that cannot work", () => {
    expect(pairingAddressProblem("https://relay.example.org")).toBeNull();
    expect(pairingAddressProblem("http://127.0.0.1:8420")).toContain("phone cannot open");
    const tailscale = pairingAddressProblem("http://100.65.64.86:8431");
    expect(tailscale).toContain("plain http");
    expect(tailscale).toContain("Tailscale");
    expect(pairingAddressProblem("garbage")).not.toBeNull();
  });
});
