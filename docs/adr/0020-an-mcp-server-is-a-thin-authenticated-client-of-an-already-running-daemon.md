# ADR-0020: The MCP server is a thin, separate client process of an already-running `cuttlefish serve` — it forwards whatever token it's given, never a new auth mechanism

- Status: Accepted
- Date: 2026-09-28
- Deciders: Jian

## Context

KAN-1764 (CUT-E10, raised by Jian directly alongside the Codex backend card):
"cuttlefish already has a CLI that's fully agent-drivable via shell (any
agent can call `cuttlefish run`/`run-team`/`approve`/`steer` today) — the
real gap an MCP server closes is for MCP-native hosts (Claude Code, Claude
Desktop, other MCP clients) that prefer typed tool calls over shelling out.
Cheap to build: a thin wrapper over the fleet daemon's existing HTTP API
(`cuttlefish.fleet.server`) — start/stop/steer/approve/status map close to
1:1 onto MCP tools. Tradeoff: a second surface to keep in sync with the
CLI/HTTP API as the product evolves, and MCP's own auth model needs to
compose with ADR-0011's session auth."

Two design questions, both narrower than they first looked once the actual
shape of `cuttlefish.fleet.server`'s own HTTP API (ADR-0009) and the real
`mcp` SDK (v2.2.0, installed and inspected directly — its own `FastMCP` was
renamed `MCPServer` in this major version, not assumed from stale
documentation) were both checked directly:

**Does this need a new auth mechanism to "compose with ADR-0011"?** No —
the fleet daemon's own `x-cuttlefish-token` header already authenticates
*any* HTTP client identically, whether that's a browser (the dashboard,
ADR-0009), `curl`, or this MCP server. "Composing" just means: this process
needs to *hold* a valid token, the identical loopback static token or
non-loopback `SessionAuth` session token (ADR-0011) an operator already has
from starting `cuttlefish serve` or logging in. Nothing about MCP's own
protocol needs threading through the daemon's own auth layer at all.

**Where does this process actually run, relative to the daemon?** As its
own, separate OS process — an MCP-native host (Claude Code, Claude Desktop)
launches `cuttlefish mcp` itself, over stdio, as *a client* of an
already-running `cuttlefish serve`, never the daemon itself and never
started by it. This resolves the one real ambiguity in the card's own "thin
wrapper" framing: a thin wrapper *around what*, running *where*. Verified
live end to end (2026-09-28): a real `mcp.client` session driving a real
`cuttlefish mcp` subprocess over stdio, which made real
`urllib`-over-HTTP calls against a real running `cuttlefish serve`,
registering a project, starting a real kopicode team, and reading its real
episodic journal back through `get_events` — not just built and
unit-tested.

## Decision

**`cuttlefish.mcp.build_mcp_server(*, base_url, token)`** returns an
`mcp.server.mcpserver.MCPServer` whose eight tools are thin, one-request
wrappers over `cuttlefish.fleet.server`'s own routes — `list_projects`,
`get_project`, `register_project`, `start_project`, `stop_project`,
`steer_project`, `approve_project`, `get_events` — each just building the
right path/body and making one blocking `urllib.request` call
(`cuttlefish.mcp._request`) off the event loop via `asyncio.to_thread`, the
identical shape `cuttlefish.steering`'s own HTTP client already uses for
satay's control API. A tool that raises (`FleetApiError`/
`FleetUnreachableError`) needs no special handling here at all — the SDK's
own `MCPServer` already converts any exception a tool raises into a normal
`CallToolResult(is_error=True, ...)` its caller sees, verified directly by
reading `mcpserver/tools/base.py`'s own exception handling, not assumed.

**`cuttlefish mcp --base-url URL --token TOKEN`** (also readable from
`CUTTLEFISH_MCP_BASE_URL`/`CUTTLEFISH_MCP_TOKEN`, since an MCP host's own
launch config is typically env vars, not CLI args — Claude Desktop's/Claude
Code's own MCP config shape) runs `server.run("stdio")`, a synchronous call
(`anyio.run` internally, verified by reading the SDK's own source) — no
`asyncio.run` wrapper needed on cuttlefish's own side, matching every other
blocking CLI command's own shape (`secrets get/set`). This process never
starts, owns, or is started by a `cuttlefish serve` — it is purely a client
of one already running, holding whatever token it was configured with.

**A deliberately smaller surface than the daemon's full HTTP API** — no
`update_roles`/`update_allow`/`update_budget`/`deregister` equivalents this
slice. Those are slower-moving, reviewed-once configuration actions (Q53's
own precedent for `allow`/budget), not the run/steer/approve verbs an agent
actually drives moment to moment — every tool here is genuinely something
an agent needs mid-task. This also directly answers the card's own named
tradeoff ("a second surface to keep in sync"): a smaller surface is a
smaller thing to keep in sync as the HTTP API evolves.

**No login flow of its own.** A non-loopback `SessionAuth` token
(ADR-0011) expires (`DEFAULT_SESSION_TTL_SECONDS`, 12 hours) and this
server has no `POST /api/login` call built in to refresh it — a long-lived
MCP server process against a non-loopback daemon needs a fresh token handed
to it (a restart) once the old one lapses. Named honestly as a real,
accepted limit, not solved this slice: the loopback default's own static,
printed-once token never expires, so the common single-operator case is
entirely unaffected.

## Alternatives considered

| Option | Why not |
|--------|---------|
| The MCP server *is* the fleet daemon (or starts one), rather than a separate client of an already-running one | Would duplicate `cuttlefish.fleet.daemon`'s own project-launching/resume machinery behind a second entry point, exactly the "second, heavier surface" the card's own tradeoff already warns about — a thin HTTP client needs none of that, since the daemon already does it. |
| Invent a session/login flow inside the MCP server so it can refresh an expiring `SessionAuth` token itself | Real, plausible future work, but adds real complexity (credential storage, a refresh loop) to what the card explicitly scoped as "cheap to build... a thin wrapper" — the loopback default (an operator's own single-machine case, and the common one) never needs it at all. |
| Expose every one of the fleet daemon's own HTTP routes as an MCP tool, for full parity | Directly works against the card's own named tradeoff ("a second surface to keep in sync with the CLI/HTTP API as the product evolves") — a smaller, workflow-verb-only surface is deliberately easier to keep in sync, and covers everything "agent-drivable end to end" actually needs moment to moment. |

## Consequences

An MCP-native host can now drive cuttlefish-crew's fleet daemon with typed
tool calls instead of shelling out to the CLI — `mcp>=2.2.0` (the official
Model Context Protocol SDK) is a new, floated dependency, the same
"well-behaved, actively maintained, not cuttlefish's own to pin tightly"
posture `anthropic`/`openai`/`e2b` already hold. This is a second real
surface reading and driving the fleet daemon (the dashboard being the
first), and it will need to be kept in sync with `cuttlefish.fleet.server`'s
own routes as they evolve — a real, named cost the card itself already
flagged, mitigated but not eliminated by keeping this surface deliberately
smaller than the full HTTP API. Non-loopback `SessionAuth` token expiry
composing with a long-lived MCP server process is a real, accepted gap for
this slice, not solved.
