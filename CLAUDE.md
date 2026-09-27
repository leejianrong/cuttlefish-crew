# CLAUDE.md — agent brief for cuttlefish-crew

cuttlefish-crew is pivoting from a single-task supervisor into a fleet
manager for teams of coding sub-agents across many software projects,
dashboard-observable, with automated context handover and a hosting story
for viewing a real demo remotely. Built on
[satay-runtime](https://github.com/leejianrong/satay-runtime) for durable
workflow execution, delegating coding work through a pluggable
`AgentBackend` seam —
[kopicode](https://github.com/leejianrong/kopicode) is the reference
backend, headless Claude Code the second. Python, `uv`, `ruff`,
`mypy --strict`, `pytest` — the same toolchain conventions as satay-runtime,
since this project depends on it directly.

## Trust the code over the docs

`docs/` describes the intended system; where the two disagree, the code is
the truth — `ls src/cuttlefish/`, `git log --oneline`, and a module's own
doc comment all beat a paragraph here.

- [`docs/PLAN.md`](docs/PLAN.md) — the current problem, scope, and shape
  (the cuttlefish-crew pivot direction, built on top of V1/V2's original MVP)
- [`docs/adr/`](docs/adr/) — why each load-bearing decision was made, 0001–0014
- [`docs/SLICES.md`](docs/SLICES.md) — the build order this was built against
- [`docs/QUESTIONS.md`](docs/QUESTIONS.md) — every decision, who made it, and
  where it landed, including gaps a live run surfaced after the fact
- Pandan board `cuttlefish-agent` (key `CUT`) — build-plan progress as
  epics/stories

## What's built

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

Context/session continuity (CUT-E8, ADR-0010) is complete and merged: `cuttlefish
run`/`run-team` and `FleetDaemon.resume_pending()` resume a non-terminal run by
calling `satay.start` with its original `run_id` instead of always minting a
fresh one, closing both the CLI-crash and daemon-restart resume gaps with the
one primitive satay's own replay engine already provides; `compose_steered_text`
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

The remaining CUT-E9 card (KAN-1710, a Fly.io feasibility spike) is not yet
started.

## Known, accepted gaps — don't re-litigate

- Steering (`cuttlefish steer`) redirects at a delegation round's boundary,
  not mid-flight — a message sent while a real coding-agent invocation is
  running waits for that invocation's own natural end before it's ever
  seen (minutes, for a real task). Neither backend's headless surface
  accepts input after it starts, and racing `satay.wait_for_event` against
  an in-flight task via `satay.gather` is an unverified composition of
  satay's own primitives (`WorkflowParked` unwinds the whole workflow
  drive, not one `gather` member) — named honestly in ADR-0008, not solved.
- `ClaudeCodeBackend`'s sandboxed path only works for an operator
  authenticated via `ANTHROPIC_API_KEY`, not Claude Code's own OAuth login.
  `docs/QUESTIONS.md` Q37.
- Its declared-allowlist-to-`--allowedTools` mapping is an honest
  approximation, not full parity with kopicode's KAN-987 policy gate.
  `docs/QUESTIONS.md` Q36.
- satay-runtime is one process, one writer *per store* — but the fleet
  daemon now runs several *projects'* teams concurrently in one process
  anyway (slice D1, ADR-0009): each project gets its own
  `satay.control.run_app(data_dir=<root>/.satay)`, a fully independent
  engine/store, coexisting as plain `asyncio` tasks — no subprocess, and no
  need for satay-runtime's own Postgres/multi-worker milestone
  (satay-runtime#100), which remains just as unneeded as it was for one
  project's own `satay.gather`-based team concurrency. `docs/QUESTIONS.md`
  Q33, Q42, Q48.
- Two kopicode-backed team roles sharing one `--root` used to collide on
  kopicode's own per-working-tree session lock; `cuttlefish.team
  ._dispatch_round` now dispatches them one-at-a-time instead of via
  `satay.gather` whenever that collision would otherwise happen (Q54) — real,
  but not concurrent for that case. Real concurrent *editing* still needs
  separate checkouts per role, deliberately deferred until this fallback's
  own cost is felt (kopicode's lock itself is correctly guarding against two
  agents editing one uncommitted working tree at once, not a bug). Unaffected
  by the fleet daemon (every role in one project's team still shares that
  project's one `root`). `docs/QUESTIONS.md` Q44, Q54.
- Secrets are injected directly, never brokered — the agent process itself
  still holds every secret it's given in the clear, inside its own sandbox
  or subprocess. A credential-broker/proxy is real future work, deliberately
  deferred. `docs/QUESTIONS.md` Q34, ADR-0006.
- A daemon-launched team (`cuttlefish serve`) declares no project secrets
  beyond a backend's own ambient credential names — a project needing
  `--secret`-declared names still runs via the CLI directly, not the
  dashboard, this slice. A real, named simplification, not an oversight.
  `cuttlefish.fleet.daemon.FleetDaemon.start`'s own docstring.
- A daemon restart loses every in-memory running-team handle — there is no
  separate process supervising `cuttlefish serve` itself yet, so killing it
  necessarily ends every team it owns. A project's status view still
  renders correctly afterward from its own episodic journal. ADR-0009's own
  Consequences section.
- The dashboard's pixel art (`frontend/src/lib/pixel/`) is a hand-authored
  CSS-grid sprite, not a canvas/game-engine renderer — revisit only if a
  future slice genuinely needs many more simultaneously-animating sprites
  than that shape can hold. `docs/QUESTIONS.md` Q51.
- `OfficeScene` renders every role in one shared room per project; it does
  not (yet) render a "zoomed-out, one giant map of every project" scene —
  the portfolio view's own small-multiples cards are still the zoomed-out
  layer, unchanged in shape since D1. `docs/SLICES.md` slice D2.
- Non-loopback `cuttlefish serve` (ADR-0011) is single-operator auth, not
  multi-tenancy — one shared password, one class of session token, no
  per-user accounts or roles. Its `Origin` check keeps a loopback carve-out
  (same-machine dev traffic is always allowed) but deliberately drops the
  `Host`-header DNS-rebinding check ADR-0014's loopback guard has, since a
  non-loopback bind's legitimate `Host` values can't be reduced to "loopback
  only" — the explicit `--allow-origin` list and the session token are the
  real guard there instead. Reaching it beyond a LAN still needs the
  operator's own tunnel/VPN (KAN-1708) or a real deployment (KAN-1710); this
  card only adds the auth a non-loopback bind needs once one exists.
- `cuttlefish serve --dashboard-dir` (ADR-0012) only ever resolves to a plain
  directory path, checked out from this repo (`frontend/dist`) — the daemon
  does not (yet) carry its own static assets as an installable artifact
  (`package_data`, a wheel or container image). That packaging story is real
  future work, tied to whatever KAN-1710's Fly.io feasibility spike decides,
  not solved here.
- `scripts/install-systemd-service.sh` (ADR-0014) only ships a systemd
  `--user` unit — a Docker/container restart-policy equivalent is real,
  deferred future work, tied to whatever `Dockerfile` KAN-1710's Fly.io spike
  produces, not built here to avoid duplicating that spike's own packaging
  decisions. A `--user` unit also needs `loginctl enable-linger $USER` to
  survive the operator logging out, or it stops with their last session —
  the install script checks and warns, but does not enable it itself.
- `cuttlefish serve --tailscale` (ADR-0013) is plain HTTP, not HTTPS — an
  accepted gap, not an oversight: Tailscale's own WireGuard mesh already
  encrypts every packet between tailnet peers, so this is a browser-padlock
  limitation, not an unencrypted wire. It is also deliberately incompatible
  with `tailscale serve`'s own reverse proxy in front of a *loopback*-bound
  `cuttlefish serve` — `tailscale serve` forwards the original tailnet `Host`
  header verbatim (verified against Tailscale's own source), which satay's
  own loopback `Host` check (ADR-0014) rejects. None of ADR-0013 has been
  checked against a real, live tailnet in this environment (no `tailscale`
  binary/account available here) — named honestly, not glossed over.

## Workflow conventions

- `main` is PR-only. Branch per slice part: `git switch -c feat/<slice>-<part>`
  off `origin/main`, then open a PR. `make ci` green before merging.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- `make check` (lint + `mypy --strict`), `make test` (fast, unit-only),
  `make ci` (the full suite `make test-all`, plus `frontend-check`/
  `frontend-test`/`frontend-build`, gates on). CI additionally builds
  kopicode from source so the delegation's integration tests run against
  the real binary, not a mock.
- `make demo` (`scripts/demo.sh`) is the one-command way to actually run
  this and try it — a fleet daemon plus the dashboard's dev server
  together, printing the URL/token to paste in. Bare `make` always shows
  `make help`, never runs a target by accident (dev-playbook's own
  Makefile guidance) — keep any new target's `##` comment and this
  ordering intact.

## Boundaries that must not be crossed

These follow directly from the ADRs. Hold them without re-litigating them here.

- **The core loop is a satay workflow from the first commit that runs a
  task**, not an ordinary function made durable later. ADR-0001.
- **Episodic memory is its own SQLite store**, never a table inside satay's
  own `.satay/` database. ADR-0004.
- **No parallel transcript.** Everything a person or another tool reads back
  is derived from the episodic journal. ADR-0004.
- **The sandbox stays an internal package, not a second product**, until a
  real second consumer or concrete reason to spin it out exists. ADR-0002.
- **No new protocol for any given backend.** Each `AgentBackend` wraps its
  own tool's existing headless surface as it exists; cuttlefish-crew
  normalises on its own side (`DelegationOutcome`), never inventing a shared
  wire format between backends. ADR-0003, ADR-0005.
- **Secrets are redacted from the episodic journal at write time**, not read
  time.
- **A project-scoped secret's decrypted value never becomes a satay task
  argument or return value.** `cuttlefish.secrets.SecretsStore.resolve` is
  only ever called *inside* the already-side-effecting delegation task; the
  result is a local variable handed straight to `backend.delegate()`, never
  returned or passed to another task. ADR-0006.
- **`cuttlefish.runtime` is `contextvars`-backed, not a plain global.** The
  fleet daemon depends on this to run several projects' teams concurrently
  without cross-contaminating each other's `Runtime` (episodic store,
  secrets store, backend selection) — reverting it to a plain global would
  silently reintroduce that exact bug. ADR-0009, `docs/QUESTIONS.md` Q49.
- **A daemon-side call to satay's own control API (`stop`/`steer`) must go
  through `asyncio.to_thread`, never called directly.** The fleet daemon and
  the `satay.control.run_app` server it's calling share one process and one
  event loop (ADR-0009) — a direct, blocking `urllib` call deadlocks against
  the very server it's waiting on. `cuttlefish.fleet.daemon.FleetDaemon
  .stop`/`.steer`'s own docstrings; verified live, not just reasoned about.

## Secrets

- **Never read or open `.env`.** It holds the real `OPENROUTER_API_KEY` for
  this repo. Refer to `.env.example` instead — the committed template with
  no real values.
- **Never read or open `.cuttlefish/secrets.db`.** It's encrypted at rest,
  but still holds every project's real secrets; use `cuttlefish secrets
  get/list` instead. Never log or print `CUTTLEFISH_SECRETS_KEY` itself —
  losing it is equivalent to losing every secret it protects.
