# Known, accepted gaps

Moved out of `AGENTS.md` so the root brief stays short. Each of these was
named on purpose; don't re-litigate one without new evidence. Read this
before proposing to "fix" a limitation, and add a bullet (with the ADR or
`docs/QUESTIONS.md` entry that explains it) when a new one is accepted.

- **The one-shot CLI resumes only on request.** A plain `cuttlefish run`/
  `run-team` rerun after a crash starts a new run (verified live 2026-09-29:
  `kill -9` mid-delegation, identical rerun, new task, the crashed row stays
  non-terminal in `.satay/`); since KAN-1806 it warns on stderr naming every
  unfinished run in that directory. `--resume <id>` re-drives the old run via
  `satay.start(run_id=)`, but the operator must repeat the original arguments
  exactly (satay replays against the input it is given), a round in flight at
  the crash restarts from its start, and the warning cannot tell a crashed run
  from one still running in another process. Only the daemon resumes with no
  operator action. Don't write "a killed process resumes" without saying
  which one. ADR-0010.
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
- A `read-only` role (reviewer, planner; ADR-0024/0025) cannot edit files on Claude
  Code (edit tools denied) or Codex (read-only sandbox), but can on kopicode: its
  gate treats in-root edits as implicit and `serve` offers no way to refuse one. There
  the role's prompt is the only guard.
- **Only kopicode can ask** (ADR-0028). In Ask first and Standard a command that is not
  allowed stops a kopicode agent and waits under Needs you, for the request window (default 10
  minutes, `CUTTLEFISH_REQUEST_WINDOW`; 45 seconds on a kopicode before v0.3.0, which has no
  `--consent-timeout`).
  Claude Code and Codex cannot pause mid-run, so there the command is refused (V4-I adds a
  blocked-action card, V4-J to V4-M the live prompts). On Codex Ask first also cannot edit,
  since the only sandbox without commands is read-only. `cuttlefish run` and `run-team` have no
  inbox and refuse as before.
- **A kopicode `ask` question is live only on kopicode v0.4.0 or later**, and only where a person can
  be asked (not Auto, not a read-only role, not `cuttlefish run`/`run-team`, and only when the
  binary has `--consent-timeout`). Everywhere else the model gets its fixed "no human is present"
  reply. The live path has only run against the scripted fake `serve`, never real kopicode.
- **V4-H live checks that remain**: what a real model does after a request expires or an
  Always allow, and the `ask` event shape and the live `ask.request` answer (read from kopicode's source, never seen live). The
  hold past kopicode's default 60 seconds and the answers reaching kopicode were checked live
  (`test_kopicode_serve_needs_you_live.py`, kopicode v0.3.0).
- **Stop lands when the round ends, not at once.** satay's cancel is delivered between
  rounds, so a role mid-round keeps working (and editing) until that round finishes, which can
  take minutes. Since KAN-1896 the dashboard says "Stopping" immediately, a stopped team asks
  for nothing more (every later Needs-you request is refused), and a role cut off reads
  "stopped" instead of "blocked". Interrupting a round in flight would need kopicode's own
  `session.cancel` driven from the daemon, which is not built.
- **A pending request does not survive a restart** and is not re-asked: it is journaled as
  abandoned, and a resumed run asks again if it needs to. The window is daemon-wide, not per
  project.
- **Always allow is project-wide**, saved to the project's own commands and applied to the
  running team at once; it is not per role, and the Permissions tab's draft can overwrite it on
  Save if it was open at the time.
- Auto on Claude Code denies `curl` and `wget` outright because a deny pattern cannot
  express "piped into a shell"; kopicode's check is finer. Auto on Codex is just
  `workspace-write`: the sandbox, not a policy, holds the never-allowed list. Auto's
  write-outside-root check reads the command line as text (a script or a path built at
  run time can still write elsewhere).
- Its declared-allowlist-to-`--allowedTools` mapping is an honest
  approximation (the default presets pass through it too, so `find` and `rg`
  there are not protected from `-delete`/`--pre` the way kopicode's consent
  policy protects them; and a deny pattern beating an allow pattern is not yet
  verified live), not full parity with kopicode's KAN-987 policy gate.
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
- `KopicodeBackend`'s `DelegationOutcome.cost_usd` is the figure kopicode reports in a turn's `usage`
  (v0.4.0, seen live: about $0.66 for two rounds), which kopicode gives only when every request reported
  a cost. An older kopicode, a round cut off by a wall-clock timeout (no turn result) and a route that
  reports no cost leave it `None`; it is never estimated from a price table (rejected as
  stale-by-construction). The `max_cost_usd` ceiling therefore cannot trip for those. ADR-0017.
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
- **Environment detection reads the root and the folders directly under it, and runs nothing** (V5-E2, V5-E6b,
  ADR-0029). `frontend/package.json` and `backend/pyproject.toml` are found; `services/api/pyproject.toml` is not
  (two levels), nor anything under a hidden folder, `node_modules`, `vendor`, `target`, `dist`, `build` and the
  like. A folder whose ecosystem the root already has is skipped, because it is almost always a workspace member
  the root's own install covers (npm and pnpm workspaces, a Cargo workspace, a uv workspace): a repository with a
  root `package.json` and an unrelated `docs/package.json` therefore does not get the second one installed. At most
  12 nested projects are reported. Cuttlefish puts only the **root's** `.venv` and `node_modules/.bin` on an agent's
  `PATH`; for a nested project the brief says where it is and to `cd` there. Probing with a project's own tools
  would run its code before anyone agreed to, so "is the install in sync?" is the fingerprint, not a probe.
  `cuttlefish.fleet.fs`'s folder-picker language list is a separate, coarser set of markers.
- **What cuttlefish installs is a fixed list** (V5-E3a, V5-E6a, ADR-0029): `uv`, poetry, pipenv, the JS package
  managers, `go mod download`, `cargo fetch`, `bundle install`, Maven and Gradle. A pip project with only a
  `pyproject.toml` (no `requirements.txt`) is reported "not prepared", never silently skipped. Go, Rust, Java and
  Ruby keep their downloads outside the project, so "fetched" is only what `.cuttlefish/env.json` remembers: a
  project fetched by hand is fetched once more, and a cache someone clears is not noticed. poetry and pipenv are
  told to keep their environment in `.venv`; one a person made elsewhere is not seen, and a second is made. Gems
  go to the user's gem home, not the project, and `bundle exec` is the agent's way in. Java's Maven goal and
  Gradle task run the project's own build plugins, so they are covered by the same confirmation as `npm ci`;
  they were written against the tools' documentation and not run here (no Maven or Gradle on the machine
  that built this). An install
  runs the project's own scripts, so `ask` is the default and a person (or a client) must say `yes`;
  there is no sandbox around it, only a timeout, a scrubbed environment and a fixed program list. A failed
  install fails the team rather than starting agents without dependencies. Not covered: a stop during an
  install of a team that was resumed after a daemon restart (resume never installs).
- **An agent's environment is an allowlist, and a variable it needs may not be on it** (V5-E4, ADR-0029, Q58). `SSH_AUTH_SOCK`
  (so `git` over SSH), `AWS_*` or `GOOGLE_*` (Claude Code on Bedrock or Vertex), a private registry's token for an agent's own
  `npm install` and anything custom are not passed; name them in `CUTTLEFISH_AGENT_ENV_PASSTHROUGH`. That is deliberate:
  the other direction leaked `CUTTLEFISH_SECRETS_KEY` and the daemon's `.env` to every agent. The allowlist applies to the
  host spawns of kopicode, Claude Code and Codex; a command run inside a sandbox provider takes its environment from that
  provider. WSL's `/mnt/...` is dropped for every project or none (`CUTTLEFISH_KEEP_WINDOWS_PATH`), not per project.
- **The stuck-agent detector covers kopicode on the host only** (V5-E5, ADR-0029). Claude Code and Codex run one-shot and hand back
  their output at the end, so there is nothing to stop early. A kopicode in a sandbox keeps its record inside the container, so
  it is not watched. Detection needs the signature list to know the failure: a toolchain it does not name (add a regular
  expression to `cuttlefish.stuck.SIGNATURES`) runs to `max_turns` as before. N is consecutive failures, so an agent that
  alternates a failing install with a passing `ls` is never stopped.
- **A stuck-agent card has no button** (V5-E5b). Nothing could be answered: the round is over. The person fixes the
  environment and steers the role from the project page; the card ends when they do. A stuck role in a steerable team (the daemon's always are) waits for a steer with no timeout instead of
  the usual five-second grace, so a role nobody steers keeps the team running until it is stopped.
- **A typed answer to a question is kept as typed**: the journal scrubs known secret values from it, not
  anything key-shaped, so a credential a person types into an answer stays in `episodic.db`, satay's own
  database and the history API.
- **"No progress" means no file edit.** A role that only changes files by running a shell generator, or that
  legitimately spends rounds reading (a reviewer), can hit the no-progress stop; it counts only after a round
  ran out of room, and `CUTTLEFISH_MAX_IDLE_ROUNDS=0` turns it off. It does not notice an agent that edits the
  same file back and forth. The wall-clock limit counts time spent waiting for you in Needs you.
- **The dashboard has no URL routing.** Which screen and project are open lives in memory (`App.svelte`), so a reload or the
  browser's Back button lands on the Projects list. This is what the "opening a project while a team is blocked lands on
  Projects" report turned out to be (not reproduced by clicking in 23 tries; reload reproduces it every time). Deep links
  and Back need a router; not built.
- **A continuation's handover needs the summariser.** Each auto-continued round writes one (ADR-0030's fourth update), so a
  long team now needs `OPENROUTER_API_KEY` (or `CUTTLEFISH_LLM_PROVIDER=replay`, placeholder text); without one the write
  fails, is logged, and the next round gets the list of changed files only.
- **Limits are set for kopicode teams started by the daemon.** `cuttlefish run-team` and `run` read the environment only; Claude Code
  and Codex ignore turns, tokens, context and time (they have no such controls), so a role on them only honours its
  continuation and no-change counters. A change to a project's or role's limits applies at the next start (ADR-0030).
- **A finished role still shows the attention sprite for a few seconds.** The chip reads "finishing" (calm), but the sprite's "!" and the
  tab's "!" follow the role's `blocked` status during the short steering grace before the task ends (ADR-0008).

