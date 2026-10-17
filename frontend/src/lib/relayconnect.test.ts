import { describe, expect, it, vi } from "vitest";
import { deviceName } from "./relayconnect";

describe("deviceName", () => {
  it("names the kind of device and the browser", () => {
    const cases: [string, string][] = [
      ["Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36", "Android phone, Chrome"],
      ["Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1", "iPhone, Safari"],
      ["Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0", "Windows PC, Firefox"],
      ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36 Edg/130.0", "Windows PC, Edge"],
      ["Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15", "Mac, Safari"],
      ["something else entirely", "Device"],
    ];
    for (const [agent, name] of cases) expect(deviceName(agent)).toBe(name);
  });
});

describe("startRelay", () => {
  it("reads the link before it takes it out of the address (window.location is live)", async () => {
    const { startRelay } = await import("./relayconnect");
    const { toBase64Url } = await import("./relay");
    const key = (n: number) => toBase64Url(new Uint8Array(32).fill(n));
    const payload = toBase64Url(
      new TextEncoder().encode(
        JSON.stringify({ v: 2, relay: "ws://127.0.0.1:8450", room: "r", daemon: key(1), pairing: key(2) }),
      ),
    );
    const live = { hash: `#pair=${payload}`, pathname: "/", search: "" };
    const replaced: string[] = [];
    // A page's `location` changes under you when the address is replaced.
    vi.stubGlobal("history", {
      replaceState: (_s: unknown, _t: string, url: string) => {
        replaced.push(url);
        live.hash = url.slice(url.indexOf("#"));
      },
    });
    const start = await startRelay(live);
    expect(start.kind).toBe("pair");
    expect(replaced).toEqual(["/#/projects"]);
    vi.unstubAllGlobals();
  });

  it("says what is wrong with a link it cannot read, and still takes the secret out of the address", async () => {
    const { startRelay } = await import("./relayconnect");
    vi.stubGlobal("history", { replaceState: () => undefined });
    const start = await startRelay({ hash: "#pair=not-a-link", pathname: "/", search: "" });
    expect(start).toMatchObject({ kind: "error" });
    vi.unstubAllGlobals();
  });
});

describe("startRelay on a page with no WebCrypto (plain http on a Tailscale or LAN address)", () => {
  it("says why a pairing link cannot be used there, and still takes the secret out of the address", async () => {
    const { startRelay } = await import("./relayconnect");
    const replaced: string[] = [];
    vi.stubGlobal("history", { replaceState: (_s: unknown, _t: string, url: string) => replaced.push(url) });
    vi.stubGlobal("isSecureContext", false);
    vi.stubGlobal("crypto", {});
    const start = await startRelay({ hash: "#pair=anything", pathname: "/", search: "" });
    expect(start).toMatchObject({ kind: "error" });
    expect((start as { message: string }).message).toContain("plain http address");
    expect(replaced).toEqual(["/#/projects"]);
    vi.unstubAllGlobals();
  });

  it("does not try a remembered pairing there either", async () => {
    const { startRelay } = await import("./relayconnect");
    vi.stubGlobal("isSecureContext", false);
    vi.stubGlobal("crypto", {});
    expect(await startRelay({ hash: "", pathname: "/", search: "" })).toEqual({ kind: "none" });
    vi.unstubAllGlobals();
  });
});
