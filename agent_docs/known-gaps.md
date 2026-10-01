# Known, accepted gaps

Moved out of `CLAUDE.md` so the root brief stays short. Each of these was
named on purpose; don't re-litigate one without new evidence. Read this
before proposing to "fix" a limitation, and add a bullet (with the ADR or
`docs/QUESTIONS.md` entry that explains it) when a new one is accepted.

- **The one-shot CLI never resumes.** `cuttlefish run`/`run-team` always mint
  a fresh `uuid4` (`cli.py`), and ADR-0010's planned `--resume <id>` flag was
  never built. Verified live 2026-09-29: `kill -9` mid-delegation, rerun the
  identical command, get a new task; the crashed run's row stays non-terminal
  in `.satay/`. Only the daemon resumes (a mid-flight delegation round
  restarts, finished rounds are not re-run). Don't write "a killed process
  resumes" without saying which one. ADR-0010.
- satay-runtime's own `durable_wait_for_event` identity (`event#{ordinal}`,
  `satay/replay/engine.py`) discards the event type before building the final
  identity string, only keeping it in the *counter* that produces the
  ordinal — two different event types both at their own first-ever ordinal
  within one workflow execution collide on the identical identity (ADR-0016
  found this live, reproduced via `require_approval`+`steerable` in the same
  round). Cuttlefish's own fix (never await two event types in one round) is
  a real, sufficient, and arguably better design on cuttlefish's own side —
  not filed upstream against satay-runtime this slice, since it isn't
  blocking anything further and a durable-identity format change is exactly
  the kind of thing satay's own code-version/nondeterminism-policy machinery
  would need to gate carefully, not a quick ask. Worth raising with
  satay-runtime separately if a future feature genuinely needs to await two
  distinct event types in one round.
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
- `CodexBackend`'s sandboxed path fails closed on a 401 for every operator,
  not just an unauthenticated one -- verified live that `codex exec` does
  not read `OPENAI_API_KEY` as an ambient credential at all, a strictly
  harder version of `ClaudeCodeBackend`'s own OAuth gap above. ADR-0018.
- `CodexBackend`'s declared-allowlist-to-`--sandbox` mapping is binary
  (`read-only` or `workspace-write`, whatever was actually declared) --
  Codex's own sandbox flag has no per-command filtering at all, coarser
  than either other backend's own approximation. ADR-0018.
- `CodexBackend`'s "refused" classification, when no edit lands, is a
  substring heuristic on an undocumented stderr log line (`"rejected"`) --
  Codex's own event stream has no structured denial signal at all, verified
  live. Could silently stop matching if a future Codex release rewords that
  log line. ADR-0018.
- `ToolCallRecorded` events (KAN-1714) don't feed `cuttlefish.handover`'s own
  context-compaction window, and the dashboard's event log renders one row
  per call with no collapsing/grouping -- both deliberate scope boundaries
  named in ADR-0019, not oversights; revisit only if a real run's own
  per-round call volume makes either genuinely hard to use.
- `cuttlefish mcp` (KAN-1764) has no login flow of its own -- a non-loopback
  `SessionAuth` token (ADR-0011) it was launched with still expires
  (`DEFAULT_SESSION_TTL_SECONDS`, 12 hours), needing a restart with a fresh
  token once it does. The loopback default's own static token never
  expires, so this doesn't affect the common single-operator case. ADR-0020.
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
- `KopicodeBackend`'s own `DelegationOutcome.cost_usd` is always `None` --
  kopicode's headless `run --print` surface reports a summed token total per
  session (`provider_response`'s own `size` field) but no dollar figure at
  all, verified against its own source (`internal/engine/event.go`). A
  per-model pricing table to estimate one was considered and rejected as a
  stale-by-construction guess, not built. ADR-0017.
- A `max_tokens`/`max_cost_usd` ceiling (KAN-1712, ADR-0017) is checked
  against the *current run's own* cumulative usage, never a lifetime or
  calendar-window total across every run a project has started -- the
  identical run-scoped shape `token_budget`'s own handover checkpoint
  already uses. A longer-window budget is real future work, deliberately
  deferred until a persona actually needs one. ADR-0017.
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
- `Dockerfile`/`fly.toml.example` (ADR-0015) have never been deployed to
  Fly.io's actual service — built and run only locally, against a real
  Docker daemon, never against Fly's live API/edge/billing. A real deploy,
  DNS, and F4's own pricing wrapper around it are still open. One Fly app
  per tenant is the recommended shape, not a shared multi-tenant instance —
  this architecture's single-writer-per-store discipline has no shared-
  tenant story built (ADR-0002's own standing position, unchanged).
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
