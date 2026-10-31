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

describe("FleetClient.openPullRequest (CUT-241)", () => {
  it("posts whether to push first and returns the refusal as an answer", async () => {
    const answer = { ok: false, code: "not_pushed", reason: "b is not pushed to origin yet." };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(answer), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new FleetClient("http://x", "t");
    await expect(client.openPullRequest("p1", true)).resolves.toEqual(answer);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://x/api/projects/p1/pull-request");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ push: true });
  });
});

describe("FleetClient.removeGoalWorktree (CUT-250)", () => {
  it("posts whether to delete the branch and returns a refusal as an answer", async () => {
    const answer = { ok: false, code: "uncommitted", reason: "The worktree has uncommitted changes." };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(answer), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new FleetClient("http://x", "t");
    await expect(client.removeGoalWorktree("p1", true)).resolves.toEqual(answer);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://x/api/projects/p1/goal/worktree/remove");
    expect(JSON.parse(init.body as string)).toEqual({ delete_branch: true });
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

describe("FleetClient over a transport", () => {
  it("sends every request through the transport it was given, and never fetch", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const seen: { path: string; token: unknown }[] = [];
    const transport = {
      fetch: async (path: string, init?: RequestInit) => {
        seen.push({ path, token: (init?.headers as Record<string, string>)["x-cuttlefish-token"] });
        return new Response(JSON.stringify({ projects: [] }), { status: 200 });
      },
      openSocket: vi.fn(),
    };
    const client = new FleetClient("relay://abc", "relay", undefined, transport);
    await client.listProjects();
    expect(seen).toEqual([{ path: "/api/projects", token: "relay" }]);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("opens the terminal through the transport by path", () => {
    const socket = {};
    const openSocket = vi.fn(() => socket);
    const client = new FleetClient("relay://abc", "relay", undefined, { fetch: vi.fn(), openSocket } as never);
    expect(client.openTerminal("p 1")).toBe(socket);
    expect(openSocket).toHaveBeenCalledWith("/api/projects/p%201/terminal");
  });

  it("reaches a daemon directly when no transport is given", async () => {
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({ projects: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    await new FleetClient("http://127.0.0.1:8420/", "t").listProjects();
    expect(fetchSpy).toHaveBeenCalledWith("http://127.0.0.1:8420/api/projects", expect.anything());
  });
});
