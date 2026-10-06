// A thin, typed client for the fleet daemon's HTTP surface (ADR-0009,
// `cuttlefish.fleet.server`). Hand-written types mirroring the Python dataclasses
// that slice adds -- no OpenAPI-codegen machinery for five routes.

export type RoleStatus = "queued" | "working" | "blocked" | "done" | "failed";

/** A project's permission mode (ADR-0025). */
export type PermissionMode = "ask-first" | "standard" | "auto";

/** A role's access: a mode, or read-only (roles only). */
export type AccessLevel = PermissionMode | "read-only";

export interface RoleDefinition {
  name: string;
  persona: string;
  /** KAN-1809: this role's own agent backend; null/absent uses the project's. */
  backend?: string | null;
  /** V4-B/V4-C: null inherits the project's mode; otherwise this role's own level. Read-only
   * blocks edits on Claude Code and Codex but not kopicode (ADR-0025). */
  access?: AccessLevel | null;
  /** V4-B: true while a built-in role still carries its built-in prompt. Read-only. */
  default_prompt?: boolean;
}

/** One role's cumulative usage so far (KAN-1712/ADR-0017), derived from the
 * episodic journal -- `cost_usd` is `null` when no backend reported a dollar
 * figure (kopicode, Codex), which is "unknown", not "free" (KAN-1810). */
export interface RoleUsage {
  tokens: number;
  cost_usd: number | null;
}

/** A project's own run-scoped usage ceiling (KAN-1712/ADR-0017) -- `null` on
 * either field means "no ceiling," never zero. */
export interface ProjectBudget {
  max_tokens: number | null;
  max_cost_usd: number | null;
}

export interface ProjectSummary {
  id: string;
  name: string;
  root: string;
  secrets_scope: string;
  /** KAN-1809: the project's default backend; null uses the daemon's. */
  backend?: string | null;
  /** V4-C: applies from the next round. */
  mode: PermissionMode;
  roles: RoleDefinition[];
  last_team_id: string | null;
  allow: string[][];
  running: boolean;
  status: Record<string, RoleStatus>;
  budget: ProjectBudget;
  usage: Record<string, RoleUsage>;
}

export interface RoleStart {
  name: string;
  text: string;
}

export interface EpisodicEventView {
  seq: number;
  ts: string;
  event_type: string;
  payload: Record<string, unknown>;
}

export class FleetApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "FleetApiError";
  }
}

/** Thrown by `FleetClient.request` when `fetch` itself fails (daemon unreachable,
 * wrong base URL) -- distinct from `FleetApiError`, which means the daemon *did*
 * answer, just with an error status. */
export class FleetUnreachableError extends Error {
  constructor(baseUrl: string) {
    super(`couldn't reach the fleet daemon at ${baseUrl}`);
    this.name = "FleetUnreachableError";
  }
}

/** Which auth mode a running `cuttlefish serve` is in (ADR-0011): "token" is the
 * default loopback mode's static, printed-once bearer token; "password" is a
 * non-loopback bind's login/session mode, `POST /api/login` mints the session
 * token used the same way from then on. */
export type AuthMode = "token" | "password";

/** `GET /api/auth-mode` is one of two routes reachable with no credential at all
 * (`POST /api/login` is the other) -- a plain `fetch`, not `FleetClient.request`,
 * since there is no token yet to attach. */
export async function fetchAuthMode(baseUrl: string): Promise<AuthMode> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/api/auth-mode`);
  } catch {
    throw new FleetUnreachableError(baseUrl);
  }
  if (!response.ok) {
    throw new FleetApiError(response.status, response.statusText);
  }
  return ((await response.json()) as { mode: AuthMode }).mode;
}

/** `POST /api/login` (password mode only) -- exchanges the operator's password
 * for a short-lived session token, used exactly like the static token from then
 * on (the same `x-cuttlefish-token` header). */
export async function login(baseUrl: string, password: string): Promise<string> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/api/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ password }),
    });
  } catch {
    throw new FleetUnreachableError(baseUrl);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new FleetApiError(response.status, body.detail ?? response.statusText);
  }
  return ((await response.json()) as { token: string }).token;
}

export class FleetClient {
  constructor(
    public readonly baseUrl: string,
    public readonly token: string,
  ) {}

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        headers: {
          "content-type": "application/json",
          "x-cuttlefish-token": this.token,
          ...init?.headers,
        },
      });
    } catch {
      throw new FleetUnreachableError(this.baseUrl);
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      throw new FleetApiError(response.status, body.detail ?? response.statusText);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  listProjects(): Promise<{ projects: ProjectSummary[] }> {
    return this.request("/api/projects");
  }

  getProject(id: string): Promise<ProjectSummary> {
    return this.request(`/api/projects/${id}`);
  }

  getEvents(id: string): Promise<{ events: EpisodicEventView[] }> {
    return this.request(`/api/projects/${id}/events`);
  }

  registerProject(input: {
    name: string;
    root: string;
    secrets_scope?: string;
    backend?: string | null;
    /** Omit both `roles` and `template` for the default team (builder + reviewer). */
    roles?: RoleDefinition[];
    template?: string;
    mode?: PermissionMode;
    allow?: string[][];
    max_tokens?: number | null;
    max_cost_usd?: number | null;
  }): Promise<ProjectSummary> {
    return this.request("/api/projects", { method: "POST", body: JSON.stringify(input) });
  }

  updateRoles(id: string, roles: RoleDefinition[]): Promise<ProjectSummary> {
    return this.request(`/api/projects/${id}/roles`, {
      method: "PATCH",
      body: JSON.stringify({ roles }),
    });
  }

  updateMode(id: string, mode: PermissionMode): Promise<ProjectSummary> {
    return this.request(`/api/projects/${id}/mode`, {
      method: "PATCH",
      body: JSON.stringify({ mode }),
    });
  }

  updateAllow(id: string, allow: string[][]): Promise<ProjectSummary> {
    return this.request(`/api/projects/${id}/allow`, {
      method: "PATCH",
      body: JSON.stringify({ allow }),
    });
  }

  /** KAN-1712/ADR-0017: `null` on either field clears that ceiling back to
   * "unset" -- the daemon's own `/budget` route reads a missing key the same
   * way, so this always sends both explicitly. */
  updateBudget(
    id: string,
    maxTokens: number | null,
    maxCostUsd: number | null,
  ): Promise<ProjectSummary> {
    return this.request(`/api/projects/${id}/budget`, {
      method: "PATCH",
      body: JSON.stringify({ max_tokens: maxTokens, max_cost_usd: maxCostUsd }),
    });
  }

  deregisterProject(id: string): Promise<void> {
    return this.request(`/api/projects/${id}`, { method: "DELETE" });
  }

  startProject(
    id: string,
    roles: RoleStart[],
    requireApproval = false,
  ): Promise<{ team_id: string }> {
    return this.request(`/api/projects/${id}/start`, {
      method: "POST",
      body: JSON.stringify({ roles, require_approval: requireApproval }),
    });
  }

  stopProject(id: string): Promise<{ status: string }> {
    return this.request(`/api/projects/${id}/stop`, { method: "POST" });
  }

  steerProject(id: string, role: string, text: string): Promise<{ status: string }> {
    return this.request(`/api/projects/${id}/steer`, {
      method: "POST",
      body: JSON.stringify({ role, text }),
    });
  }

  /** KAN-1711: `comment` is mandatory when `approved` is false (the daemon's own
   * `/approve` route rejects a comment-less rejection with a 400) and optional
   * otherwise. */
  approveProject(
    id: string,
    role: string,
    approved: boolean,
    comment?: string,
  ): Promise<{ status: string }> {
    return this.request(`/api/projects/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ role, approved, comment: comment ?? null }),
    });
  }
}
