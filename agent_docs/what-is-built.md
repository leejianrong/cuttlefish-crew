# What's built

Moved out of `CLAUDE.md` so the root brief stays short. This is a history of
shipped slices, kept for the *why* behind each; `git log --oneline`,
`docs/adr/` (0001-0021 and counting) and each module's own doc comment are
the authority, and anything here that disagrees with the code is stale.
Slices after KAN-1764 (for example ADR-0021, kopicode over `serve`) are in
`git log` and the ADRs, not below.

V1, V2 (a durable, sandboxed kopicode delegation), slice A (the pluggable
`AgentBackend` seam — `KopicodeBackend` and `ClaudeCodeBackend`, selected via
`CUTTLEFISH_AGENT_BACKEND`), slice B (`cuttlefish.secrets.SecretsStore` —
an encrypted-at-rest, project-scoped secrets store injected through each
backend's own `_credential_envs`), slice C in full — both the
team-concurrency half (`cuttlefish.team.run_team` — N named roles delegating
concurrently via `satay.gather`, `cuttlefish run-team --role NAME:TASK_TEXT`)
and the steering half (`cuttlefish run --steerable`/`run-team --steerable`
open `satay.control.run_app` and print a `base_url`/token;
`cuttlefish steer <task-id> "<message>" [--role NAME]` delivers a
`SteeringMessage` that redirects a still-running task at the boundary
between delegation rounds, not mid-flight, ADR-0008) — and slice D1
(ADR-0009: a formal `Project` entity — `cuttlefish.projects.ProjectStore`,
`~/.cuttlefish/projects.db` — plus the fleet daemon, `cuttlefish.fleet
.FleetDaemon`/`cuttlefish serve`, which launches and owns every registered
project's team concurrently as in-process `asyncio` tasks, no subprocess
per project, each its own `satay.control.run_app` pointed at that project's
own `<root>/.satay`; a loopback-only FastAPI surface
(`cuttlefish.fleet.server`) a Svelte + TypeScript + Vite dashboard
(`frontend/`) talks to for start/stop/steer/status) — are complete and
merged; `make ci` is green on `main` (now gating the `frontend/` build too).
Start reading the code at `cuttlefish/workflow.py` (the single-task core
loop), `cuttlefish/team.py` (the multi-role loop), `cuttlefish/steering.py`
(the steering wire contract and CLI-facing pointer file/HTTP client),
`cuttlefish/agents/` (the backend seam), `cuttlefish/secrets/` (the secrets
store), `cuttlefish/projects/` (the `Project` registry), and
`cuttlefish/fleet/` (the daemon and its HTTP surface) — each module's own
doc comment explains why it exists, not a list here. `cuttlefish/config.py`
holds the config-resolution logic `cuttlefish.cli` and `cuttlefish.fleet`
both share (ADR-0009's own factoring, extending ADR-0007's). Slice D2 (the
pixel-art skin, ADR-0009) is also complete and merged — every role renders
as a small animated pixel-art sprite (`frontend/src/lib/pixel/`), a plain
CSS grid, no game-rendering library (Q51); `cuttlefish.fleet.server
.find_free_port` makes `cuttlefish serve` auto-pick a free port instead of
failing when its default is taken (Q52); `make demo`/`scripts/demo.sh` is
the one-command way to run a daemon and the dashboard together locally
(dev-playbook's own "runnable in one command" guidance). Live-verification
history and real bugs a live run found and fixed are in each PR's own
description and `docs/QUESTIONS.md`, not repeated here.

A real 2-role dashboard run against a real repo then surfaced two D1 gaps,
both now fixed (`docs/SLICES.md`'s "D1 live-usage fixes", Q53/Q54):
`Project.allow` is a persisted, per-project shell-command allowlist (same
shape as `persona`) threaded into `FleetDaemon.start`'s `RoleInput`s, so a
daemon-started team is no longer stuck with `DEFAULT_SHELL_ALLOWLIST`
regardless of what the project declares; and `cuttlefish.team
._dispatch_round` falls back to one-at-a-time dispatch, instead of
`satay.gather`, whenever two or more active roles in a round are all
kopicode-backed (every role in a team already shares one `root`), avoiding
kopicode's own per-working-tree lock collision (Q44) rather than failing
closed on it.

Context/session continuity (CUT-E8, ADR-0010) is merged for the daemon only:
`FleetDaemon.resume_pending()` resumes a non-terminal team on `cuttlefish serve`
startup by calling `satay.start` with its original `run_id`, the one primitive
satay's own replay engine already provides (the one-shot CLI only on request via
`--resume <id>`, KAN-1806, see Known gaps); `compose_steered_text`
now folds in the latest `HandoverWritten` summary instead of an unbounded raw
`round_summaries` list; the dashboard surfaces a `TeamResumed` marker and a
per-role checkpoint timeline.

Slice E (remote access & always-on hosting, CUT-E9) has started: KAN-1706 (ADR-
0011) is complete and merged — a non-loopback `cuttlefish serve --host <addr>`
bind requires `CUTTLEFISH_SERVE_PASSWORD` and switches onto
`cuttlefish.fleet.auth.SessionAuth` (a password login minting short-lived,
HMAC-signed session tokens, in-memory lockout after repeated failures) instead
of the loopback default's shared, printed-once static token
(`satay.control.SecurityPolicy`, unchanged); `--allow-origin` (repeatable) adds
explicit non-loopback browser origins to the dashboard's existing loopback-only
CORS allow-list. The dashboard's `ConnectScreen` calls the new, credential-less
`GET /api/auth-mode` once a base URL is entered, then renders a password field
(`POST /api/login`) or the classic token field accordingly.

KAN-1707 (ADR-0012) is also complete and merged: `cuttlefish serve` optionally
mounts the dashboard's own production build (`frontend/dist`, `npm run build`)
as static files at `/`, same origin as the JSON API, registered last so every
`/api/...` route still wins; `_check_security` now only ever checks `/api/...`
paths, so the static shell itself needs no credential (every API call it makes
is still gated exactly as before). `--dashboard-dir PATH` is explicit and
strict (a startup error if it has no `index.html`); with no flag,
`cuttlefish._resolve_dashboard_dir` silently probes `./frontend/dist` and
falls back to API-only if it isn't there. `make demo`/`scripts/demo.sh` is one
process now, not two — it builds the dashboard once, then `cuttlefish serve`
alone serves both; developing the frontend itself (hot reload) still means
running `frontend`'s own `npm run dev` and `cuttlefish serve` separately.

KAN-1708 (ADR-0013) is also complete and merged: `cuttlefish serve --tailscale`
binds directly to this machine's own tailnet IPv4 address
(`cuttlefish._resolve_tailscale_host`, `tailscale ip -4`), reusing ADR-0011's
non-loopback/password mode as-is — deliberately *not* fronted by `tailscale
serve`'s own reverse proxy, which forwards the original tailnet `Host` header
verbatim to a loopback backend (verified directly against Tailscale's own
source) and would break satay's own loopback `Host` check. `SessionAuth`'s
`allowed_origins` always includes the daemon's own bind origin
(`cuttlefish.fleet.server._self_origin`) in addition to `--allow-origin`, so
opening the printed URL directly on the tailnet needs no extra flag. Unverified
against a real, live tailnet (no `tailscale` binary/account in this
environment) — named honestly in ADR-0013, not glossed over.

KAN-1709 (ADR-0014) is also complete and merged: `scripts/install-systemd-service.sh`
(`make install-systemd-service`) installs `cuttlefish serve` as a systemd
`--user` unit (`Restart=on-failure`), so it auto-restarts instead of needing a
human to notice it died — the infra half of ADR-0009's own named daemon-
restart gap, composing with KAN-1703's already-shipped `resume_pending`
(the continuity half). Docker/container restart policies are deferred to
KAN-1710, which needs a `Dockerfile` for its own, separate reason. Live-
verified in this sandbox: the real installed unit, `kill -9`'d and confirmed
auto-restarting with a fresh `MainPID` within seconds, registered-project
state intact on the restarted process — not a genuinely in-flight team's
resume specifically (that composition is `FleetDaemon.resume_pending`'s own
already-tested scope, not re-derived here); fully cleaned up afterward.

KAN-1710 (ADR-0015) closes out CUT-E9 (Slice E is now fully complete): a
`Dockerfile` (three stages — frontend build, kopicode-from-source build
mirroring CI's own approach, the Python runtime) and `fly.toml.example`, both
built and run only *locally* against a real Docker daemon in this sandbox —
never deployed to Fly's actual service (Jian's own explicit scope decision,
since `flyctl` was already authenticated to his real account and provisioning
real infrastructure needed his sign-off, not an autonomous default). Two real
bugs found and fixed by actually building the image: a missing `README.md`
copy broke `hatchling`'s own build (`pyproject.toml`'s `readme` field), and
the original `CMD`'s plain `uv run` re-synced the *full* dependency group
(including `dev`) from the network on every container start — fixed with
`uv run --no-sync`. Findings: one Fly app per tenant (this architecture's
single-writer-per-store discipline has no shared-multi-tenant story to
build), a single persistent volume with `HOME` *and* every registered
project's own `--root` living under it, Fly's own secrets composing cleanly
with ADR-0011's env-var password, free TLS at Fly's edge (solving ADR-0013's
"no TLS" gap for this case specifically), `--allow-origin` for the external
`<app>.fly.dev` origin being mandatory (ADR-0013's self-origin-trust default
doesn't cover it), and scale-to-zero composing correctly with ADR-0010's own
crash-safety story. Feeds F4's hosted-service pricing/wrapper card directly.

CUT-E10 (closing the Paperclip functionality gaps) has started: KAN-1711
(ADR-0016) is complete and merged — a round-boundary approval gate
(`cuttlefish approve <task-id> [--role NAME] [--reject "<comment>"]`, a
dashboard Approve/Reject panel, `--require-approval` on `run`/`run-team`,
`FleetDaemon`/`ProjectStore` persistence of the flag for resume-fidelity) that
blocks a round from finalizing — no timeout — until an operator decides,
matching Paperclip's own issue-boundary review-gate shape. `ApprovalDecision`
is a new episodic event, delivered over the identical `SteeringMessage`
pointer-file/HTTP channel ADR-0008 already built. Two real bugs found only by
running this in a real browser against a real daemon, neither caught by an
extensive synthetic test suite beforehand: (1) a genuine satay-level
wait-identity collision (`event#N`, a bare ordinal with no type
discriminator, `satay/replay/engine.py`) when both a `SteeringMessage` and an
`ApprovalDecision` wait were awaited in the same round — exactly what every
daemon-started team does, since `steerable=True` is unconditional there
(ADR-0009); fixed by making `require_approval` *replace* the steering wait
for a gated round rather than compose with it, a real design simplification,
not just a workaround. (2) `cuttlefish.fleet.status._status_from` mapped a
bare `DelegationFailed` straight to `"failed"` instead of `"blocked"`, making
the dashboard's own approval panel (which only renders for `"blocked"`)
invisible for exactly the role that most needed it. Both fixes have dedicated
regression tests and were re-verified live afterward.

KAN-1712 (ADR-0017) is also complete and merged: per-role/per-project token and
cost tracking, riding entirely on `DelegationOutcome`'s own new `tokens`/
`cost_usd` fields (`ClaudeCodeBackend` reports both, verified live against the
real `result` event's `usage`/`total_cost_usd`; `KopicodeBackend` reports
`tokens` only, summed from `provider_response` lines its own headless surface
was silently dropping before this slice — kopicode reports no dollar figure at
all, verified against its own source, so `cost_usd` stays honestly `None`
rather than a fabricated pricing-table estimate) and the identical
`DelegationCompleted`/`DelegationRefused`/`DelegationFailed` episodic events
every round already journals, no new event type. `cuttlefish.budget` sums a
role's cumulative usage and compares it to an optional, run-scoped
`max_tokens`/`max_cost_usd` ceiling (`run`/`run-team --max-tokens`/
`--max-cost-usd`, a `Project`'s own persisted budget for daemon-started teams,
mirroring `allow`'s Q53 precedent); crossing it forces the exact
`ApprovalDecision` wait `--require-approval` already uses — never a new wire
type, sidestepping ADR-0016's own wait-identity collision by construction.
`FleetDaemon.usage`/`PATCH /api/projects/{id}/budget`/`RoleSteerCard.svelte`'s
own usage line surface this on the dashboard, reusing the identical
Approve/Reject panel a budget-triggered block needs no new UI to answer.
Live-verified in an isolated `$HOME` against a real `cuttlefish serve`: a
zero-token-ceiling project blocked on its very first round, rendered
`BLOCKED` with an over-budget usage line, and finalized correctly once
approved through the dashboard.

KAN-1713 (ADR-0018) is also complete and merged: `cuttlefish.agents.codex
.CodexBackend` is a third `AgentBackend`, proving the pluggable seam (ADR-0005)
generalizes a second time. Every fact behind it was verified live against the
real `codex` binary (2026-09-28, `codex-cli` 0.155.1), not assumed — three real
findings shaped the design: Codex's own `--sandbox {read-only,workspace-write,
danger-full-access}` has no per-command filtering at all (coarser than either
existing backend's own policy mapping, an honest gap named rather than
papered over); a permission denial produces no structured event on its own
`--json` stream, only an unstructured stderr log line, so `classify_stream`
falls back to a stderr substring heuristic when no edit landed (a genuine
`turn.failed` event *does* exist and is used for real failures, the same
clean signal the other two backends already have); and `codex exec` does not
read `OPENAI_API_KEY` as an ambient credential at invocation time at all
(verified live with a real, if fake, key producing an identical 401 to no key
at all) — a strictly harder version of `ClaudeCodeBackend`'s own already-
accepted OAuth gap (Q37), so sandboxed Codex delegation fails closed on
auth, not solved this slice. `DelegationOutcome.tokens` sums `usage
.input_tokens`/`output_tokens` only (verified live that the cache/reasoning
sub-fields read as a breakdown, not an additive pool); `cost_usd` stays
`None` always, the identical honest gap kopicode's own backend already holds
(ADR-0017) — Codex reports no dollar figure at all. `CUTTLEFISH_AGENT_BACKEND
=codex`/`CUTTLEFISH_CODEX_BIN` select it, mirroring Claude Code's own config
shape exactly. Live-verified: a real edit landing and a real sandbox refusal
both correctly classified against the real binary before merging.

KAN-1714 (ADR-0019) is also complete and merged: `cuttlefish.agents.outcome
.ToolCallRecord`/`cuttlefish.episodic.events.ToolCallRecorded` add per-tool-
call tracing underneath each round's own single verdict -- no new store,
riding on the exact per-call data every backend's own `classify_stream` was
already parsing and discarding. Each backend's own pairing was verified live,
not assumed: kopicode's `tool_call_parsed`/`tool_result` FIFO-pair one call at
a time (a `permission_decided`/deny since the last result marks the *next*
one `"denied"` rather than a generic error); Claude Code's `tool_use`/
`tool_result` content blocks pair by `tool_use_id`, not FIFO order (unverified
either way, so the id is used instead), with the final `result` event's own
`permission_denials` upgrading matching calls to `"denied"` in a second pass;
Codex's `item.completed` already carries an item's final state, no
started/completed pairing needed, and a rejected patch never produces a
`file_change` item at all (ADR-0018's own finding). `run_task`/`run_team` both
journal one `ToolCallRecorded` per call right after that round's own outcome
event; `EventLog.svelte` renders each with a status-based tint (denied/
error), reusing the existing per-row event-log shape rather than a new
grouped UI. Deliberately does not feed
`cuttlefish.handover`'s own context-compaction window -- a named scope
boundary, not an oversight. Live-verified end to end (a real kopicode call
through a real running `cuttlefish serve`, an isolated `$HOME`): a successful
`write_file` and a denied `run_shell` both landed as separate, correctly
tinted `ToolCallRecorded` rows in the dashboard's own event log.

KAN-1764 (ADR-0020) closes out CUT-E10 (raised by Jian directly, alongside
the Codex backend card): `cuttlefish mcp` is an MCP server (stdio transport,
the official `mcp` SDK v2.2.0) wrapping an already-running `cuttlefish
serve`'s own HTTP API -- a separate client process, never the daemon itself
and never started by it. Eight tools (`list_projects`/`get_project`/
`register_project`/`start_project`/`stop_project`/`steer_project`/
`approve_project`/`get_events`) map close to 1:1 onto `cuttlefish.fleet
.server`'s own routes, each one blocking `urllib` call off the event loop
via `asyncio.to_thread` -- the identical shape `cuttlefish.steering`'s own
HTTP client already uses. No new auth mechanism: this process just holds
and forwards whichever `x-cuttlefish-token` it's given (`--base-url`/
`--token`, or `CUTTLEFISH_MCP_BASE_URL`/`CUTTLEFISH_MCP_TOKEN`), the
identical bearer token the dashboard already authenticates with (ADR-0011).
A deliberately smaller surface than the daemon's full HTTP API -- no
`update_roles`/`update_allow`/`update_budget`/`deregister` equivalents,
since those are reviewed-once configuration, not moment-to-moment workflow
verbs. Live-verified end to end: a real `mcp.client` session driving a real
`cuttlefish mcp` subprocess over stdio, registering a project, starting a
real kopicode team, and reading its real episodic journal back through
`get_events` -- not just built and unit-tested.
