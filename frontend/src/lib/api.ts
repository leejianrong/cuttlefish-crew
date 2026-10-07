// A thin, typed client for the fleet daemon's HTTP surface (ADR-0009,
// `cuttlefish.fleet.server`). Hand-written types mirroring the Python dataclasses
// that slice adds -- no OpenAPI-codegen machinery for five routes.

export type RoleStatus = "queued" | "working" | "blocked" | "done" | "failed" | "stopped";

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

/** One folder in the picker (V4-E). */
export interface FolderEntry {
  name: string;
  path: string;
  is_git: boolean;
}

export interface FolderListing {
  path: string;
  root: string;
  roots: string[];
  /** null at a browse root: there is nowhere further up to go. */
  parent: string | null;
  truncated: boolean;
  folders: FolderEntry[];
}

export interface FolderInspection {
  path: string;
  name: string;
  is_git: boolean;
  branch: string | null;
  /** null when git could not say (not a repo, or it timed out). */
  dirty: boolean | null;
  last_commit: string | null;
  languages: string[];
}

export interface TeamTemplate {
  name: string;
  title: string;
  summary: string;
  roles: string[];
}

/** A built-in role from the library (V4-B). */
export interface BuiltinRole {
  name: string;
  summary: string;
  prompt: string;
  access: "standard" | "read-only";
}

export interface PermissionPreset {
  name: string;
  title: string;
  summary: string;
  /** Command prefixes, each as one line ("uv run pytest"). */
  commands: string[];
  default: boolean;
}

/** What the Permissions tab shows that the daemon owns, so the screen cannot drift from it. */
export interface PermissionsCatalog {
  modes: { name: PermissionMode; title: string; summary: string }[];
  presets: PermissionPreset[];
  never_allowed: { label: string; summary: string }[];
  backends: { name: string; when: string; summary: string }[];
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

/** One ecosystem found at a project's root (V5-E2): read from its files, nothing is run. */
export interface EcosystemEnv {
  ecosystem: "python" | "node" | "go" | "rust" | "java" | "ruby";
  tool: string | null;
  manifests: string[];
  lockfile: string | null;
  /** A version the project asks for, as it wrote it (`3.12`, `>=20`). */
  version_hint: string | null;
  env_dir: string | null;
  /** True when its own install (`.venv`, `node_modules`) is there, false when it is missing,
   * null when there is nothing in the project folder to look for. */
  installed: boolean | null;
  notes: string[];
}

export interface EnvironmentSpec {
  root: string;
  /** False when the project's folder is not there at all, which is not "nothing recognised". */
  root_exists: boolean;
  ecosystems: EcosystemEnv[];
}

export interface ProjectSummary {
  id: string;
  name: string;
  root: string;
  secrets_scope: string;
  /** KAN-1809: the project's default backend; null uses the daemon's. */
  backend?: string | null;
  /** V4-C: applies the next time the team starts, not to a team already running. */
  mode: PermissionMode;
  /** V4-F: the command groups switched on (names from the permissions catalogue). */
  presets: string[];
  roles: RoleDefinition[];
  last_team_id: string | null;
  /** The last team was started with a review gate: a blocked role is then waiting for you. */
  require_approval: boolean;
  allow: string[][];
  running: boolean;
  /** The operator asked the team to stop and its round has not ended yet. */
  stopping: boolean;
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

/** What a person may answer a request with (ADR-0028). */
export type RequestAnswer = "allow_once" | "allow_always" | "deny";

export type RequestResolution =
  | "allowed_once"
  | "allowed_always"
  | "denied"
  | "expired"
  | "cancelled"
  | "abandoned";

/** Something that needs a person: today a kopicode agent paused on a command (kind `permission`). */
export interface NeedsYouRequest {
  id: string;
  kind: "permission" | "question" | "blocked";
  /** `pending`, or how it ended. */
  state: "pending" | RequestResolution;
  title: string;
  /** The command line or the question, already redacted. */
  detail: string;
  why: string;
  answers: string[];
  suggested_rule: string[] | null;
  role: string | null;
  backend: string | null;
  /** When an answer takes effect: `now` is a live prompt, the others are not. */
  lands: "now" | "end_of_turn" | "next_round";
  expires_at: string;
  project_id: string;
  project_name: string;
  /** Pending only: seconds left when the daemon answered. */
  expires_in_s?: number;
  /** Resolved only. */
  by?: "person" | "timeout" | "system";
  rule?: string[] | null;
  raised_at?: string;
  resolved_at?: string | null;
}

export interface AnswerResult {
  request_id: string;
  resolution: RequestResolution;
  by: "person" | "timeout" | "system";
  rule: string[] | null;
  already: boolean;
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

  /** Subfolders of `path` (default: the first browse root). 403 outside the browse roots. */
  listFolders(path?: string): Promise<FolderListing> {
    const query = path ? `?path=${encodeURIComponent(path)}` : "";
    return this.request(`/api/fs${query}`);
  }

  inspectFolder(path: string): Promise<FolderInspection> {
    return this.request(`/api/fs/inspect?path=${encodeURIComponent(path)}`);
  }

  listPermissions(): Promise<PermissionsCatalog> {
    return this.request("/api/permissions");
  }

  listBuiltinRoles(): Promise<{ roles: BuiltinRole[] }> {
    return this.request("/api/roles");
  }

  updatePresets(id: string, presets: string[]): Promise<ProjectSummary> {
    return this.request(`/api/projects/${id}/presets`, {
      method: "PATCH",
      body: JSON.stringify({ presets }),
    });
  }

  listTemplates(): Promise<{ default: string; templates: TeamTemplate[] }> {
    return this.request("/api/templates");
  }

  listProjects(): Promise<{ projects: ProjectSummary[] }> {
    return this.request("/api/projects");
  }

  getProject(id: string): Promise<ProjectSummary> {
    return this.request(`/api/projects/${id}`);
  }

  getEnvironment(id: string): Promise<EnvironmentSpec> {
    return this.request(`/api/projects/${id}/environment`);
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
    presets?: string[];
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

  /** Every pending request across the fleet (the rail badge and the Needs-you screen). */
  listRequests(): Promise<{ requests: NeedsYouRequest[] }> {
    return this.request("/api/requests");
  }

  listProjectRequests(
    id: string,
  ): Promise<{ pending: NeedsYouRequest[]; resolved: NeedsYouRequest[] }> {
    return this.request(`/api/projects/${id}/requests`);
  }

  /** `rule` is only for `allow_always`: the start of the command, which the daemon checks. A 409
   * means the request already ended another way; a 422's message says why a rule was refused. */
  answerRequest(
    projectId: string,
    requestId: string,
    answer: RequestAnswer,
    rule?: string[],
  ): Promise<AnswerResult> {
    return this.request(`/api/projects/${projectId}/requests/${requestId}/answer`, {
      method: "POST",
      body: JSON.stringify(rule ? { answer, rule } : { answer }),
    });
  }
}
