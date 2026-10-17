// Unit: `fetchAuthMode`/`login` (ADR-0011) -- both are plain, credential-less
// `fetch` wrappers (no `FleetClient` involved yet), so a mocked global `fetch` is
// enough; the real HTTP behaviour they talk to is covered server-side by
// `tests/integration/test_fleet_server_auth.py`.

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  FleetApiError,
  FleetClient,
  FleetUnreachableError,
  TOKEN_REFRESH_HEADER,
  fetchAuthMode,
  login,
} from "./api";

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

describe("FleetClient session refresh (ADR-0038)", () => {
  const reply = (headers: Record<string, string> = {}, status = 200) =>
    vi.fn(async () => new Response(JSON.stringify({ projects: [] }), { status, headers }));

  it("keeps a fresh token the daemon sends and tells the app", async () => {
    vi.stubGlobal("fetch", reply({ [TOKEN_REFRESH_HEADER]: "new-token" }));
    const seen: string[] = [];
    const client = new FleetClient("http://x", "old-token", (token) => seen.push(token));
    await client.listProjects();
    expect(client.token).toBe("new-token");
    expect(seen).toEqual(["new-token"]);
  });

  it("sends the new token on the next request", async () => {
    const fetchMock = reply({ [TOKEN_REFRESH_HEADER]: "new-token" });
    vi.stubGlobal("fetch", fetchMock);
    const client = new FleetClient("http://x", "old-token");
    await client.listProjects();
    await client.listProjects();
    const second = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect((second[1].headers as Record<string, string>)["x-cuttlefish-token"]).toBe("new-token");
  });

  it("leaves the token alone when no header comes back", async () => {
    vi.stubGlobal("fetch", reply());
    const seen: string[] = [];
    const client = new FleetClient("http://x", "old-token", (token) => seen.push(token));
    await client.listProjects();
    expect(client.token).toBe("old-token");
    expect(seen).toEqual([]);
  });

  it("reports a rejected token as a 401 the app can act on", async () => {
    vi.stubGlobal("fetch", reply({}, 401));
    const client = new FleetClient("http://x", "stale");
    await expect(client.listProjects()).rejects.toMatchObject({ status: 401 });
  });
});
