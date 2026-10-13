// Unit: the address the Connect screen starts with (session.ts, CUT-106).

import { describe, expect, it } from "vitest";
import { FALLBACK_BASE_URL, defaultBaseUrl } from "./session";

describe("defaultBaseUrl", () => {
  it("is the origin that served the page, so a daemon on any port is found", () => {
    expect(defaultBaseUrl("http://127.0.0.1:8421", false)).toBe("http://127.0.0.1:8421");
    expect(defaultBaseUrl("https://crew.example.ts.net", false)).toBe("https://crew.example.ts.net");
  });

  it("falls back to the default port under the dev server, or with no usable origin", () => {
    expect(defaultBaseUrl("http://localhost:5173", true)).toBe(FALLBACK_BASE_URL);
    expect(defaultBaseUrl("null", false)).toBe(FALLBACK_BASE_URL);
    expect(defaultBaseUrl("file://", false)).toBe(FALLBACK_BASE_URL);
    expect(defaultBaseUrl(undefined, false)).toBe(FALLBACK_BASE_URL);
  });
});
