// Unit: `fetchAuthMode`/`login` (ADR-0011) -- both are plain, credential-less
// `fetch` wrappers (no `FleetClient` involved yet), so a mocked global `fetch` is
// enough; the real HTTP behaviour they talk to is covered server-side by
// `tests/integration/test_fleet_server_auth.py`.

import { afterEach, describe, expect, it, vi } from "vitest";
import { FleetApiError, FleetUnreachableError, fetchAuthMode, login } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchAuthMode", () => {
  it("returns the mode a daemon reports", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ mode: "password" }), { status: 200 })),
    );
    await expect(fetchAuthMode("http://127.0.0.1:8420")).resolves.toBe("password");
  });

  it("throws FleetUnreachableError when fetch itself fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );
    await expect(fetchAuthMode("http://127.0.0.1:8420")).rejects.toBeInstanceOf(
      FleetUnreachableError,
    );
  });

  it("throws FleetApiError on a non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("nope", { status: 500, statusText: "Server Error" })),
    );
    await expect(fetchAuthMode("http://127.0.0.1:8420")).rejects.toBeInstanceOf(FleetApiError);
  });
});

describe("login", () => {
  it("returns the minted session token on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ token: "abc.def" }), { status: 200 })),
    );
    await expect(login("http://127.0.0.1:8420", "correct-password")).resolves.toBe("abc.def");
  });

  it("throws FleetApiError with the daemon's own detail on a wrong password", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "invalid password" }), { status: 401 }),
      ),
    );
    await expect(login("http://127.0.0.1:8420", "wrong")).rejects.toMatchObject({
      status: 401,
      message: "invalid password",
    });
  });

  it("throws FleetUnreachableError when fetch itself fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );
    await expect(login("http://127.0.0.1:8420", "whatever")).rejects.toBeInstanceOf(
      FleetUnreachableError,
    );
  });
});
