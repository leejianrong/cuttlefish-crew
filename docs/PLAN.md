# cuttlefish-crew: Plan

Status: agreed and delivered - slice A (pluggable agent backend + rename,
PR #21), slice B (project/agent-scoped secrets management, ADR-0006), and
slice C in full - both the team-concurrency half (`cuttlefish run-team`,
ADR-0007) and the steering half (`cuttlefish run --steerable`/`run-team
--steerable`/`cuttlefish steer`, ADR-0008) - are all complete and merged.
Supersedes the single-task-MVP framing this document held through V1/V2
(both complete, both merged to `main` - see `agent_docs/what-is-built.md` for
that history, which stays true and is not being redone, only built on).
Slice C's steering half redirects at a delegation round's boundary, not
mid-flight (ADR-0008's own honestly-named limit). Slice D (the
dashboard/office UI) is split into **D1** - a formal `Project` entity, the
fleet daemon (`cuttlefish serve`), and a plain (non-game) UI wired to real
data - and **D2**, the pixel-art skin on top of D1's already-proven API
(ADR-0009). Both are complete and merged: each role now renders as an
animated pixel-art sprite (`frontend/src/lib/pixel/`) whose pose/tint
reflects its live status, no new rendering dependency (Q51); `cuttlefish
serve` auto-picks a free port (Q52); `make demo`/`scripts/demo.sh` is the
one-command way to run a daemon and the dashboard together locally. A real
2-role dashboard run against a real repo then surfaced two gaps (Q53, Q54),
both now fixed: a persisted, per-project `allow` field so a daemon-started
team can actually run the shell commands its own project declares, and a
sequential-dispatch fallback for kopicode-backed teams so a 2+-role team
sharing one `root` no longer collides on kopicode's own per-working-tree
lock (`docs/SLICES.md`'s "D1 live-usage fixes").

## Problem

V1/V2 proved the mechanism this project bet on: a `@satay.workflow` core loop
can survive a crash mid-delegation and resume correctly, and a single
delegated coding task, gated by a real policy and (optionally) a real
sandbox, can run unattended end to end against a live kopicode binary. That
was the riskiest unknown, and it held up.

What it didn't touch is the actual, lived pain of running coding agents day
to day: babysitting a single agent's context window (watching it bloat, then
manually clearing or starting a new session before it does), and babysitting
its *output* - the UI, the demo, the actual user-facing behavior - because a
green test suite doesn't tell you the product is right. Both of those are
single-agent, single-project problems, and they don't go away just because
the delegation survives a crash. Worse, they compound the moment there's more
than one project worth running unattended at once: there's no single place
to see what every project's agents are doing, whether any of them are stuck
on a context ceiling, or whether what they built actually looks right,
without being at the keyboard for each one.

## Solution

cuttlefish-crew runs a small team of coding sub-agents (roughly three) per
software project, across as many projects as the operator is running at
once. Each project's team has functional roles (e.g. a builder, a reviewer,
an ops/demo-checker) and a distinct personality/voice per agent, so the
record of what happened reads as a team, not a wall of uniform log lines.
Context handover across a long session is automatic, triggered on a
token-budget threshold exactly like V1's existing mechanism (ADR-0004),
generalized to run per-agent across a team instead of once per task. The
operator oversees every project's team from one dashboard, can click into a
running agent and steer its work directly rather than only reading its
history, and can view a running project's actual demo/UI remotely without
being at the machine it's running on - either through a tunnel to an
always-on machine the operator controls, or through cuttlefish-crew
provisioning a hosted, reachable deployment itself.

This milestone (slice A) builds none of that observable surface yet. It
builds the one thing everything else depends on: the coding sub-agent is no
longer hardcoded to be kopicode. A generic backend interface is introduced,
kopicode becomes its first implementation with no behavior change, and a
second backend (headless Claude Code) is implemented against the same
interface to prove the abstraction is real rather than aspirational -
directly motivated by the fact that Claude Code, not kopicode, is the agent
the operator actually babysits today.

## Users and actors

- **The operator** (primary). Runs cuttlefish-crew, configures which
  projects it oversees and what each project's team may delegate, and holds
  the credentials it uses. Was the sole intended user through V1/V2; this
  project is now explicitly meant to grow toward other operators running
  their own teams, which is a real scope expansion Q28 and ADR-0002's
  2026-09-20 addendum need to reckon with (multi-tenancy, isolation
  between operators), not something this milestone builds.
- **A project's sub-agent team** (new framing, not built this milestone).
  Roughly three agents per project, each with a functional role and a
  personality. Not a distinct trust tier from the operator's own
  configuration - same posture ADR-0002/Q23 already established for a
  single delegation, extended to a team.
- **A coding agent backend** (a dependency, pluggable). kopicode remains the
  reference implementation; a second, headless-Claude-Code-backed
  implementation is this milestone's proof that the interface isn't shaped
  around kopicode by accident. Neither backend is a distinct trust tier;
  both run inside whatever sandbox/policy the operator's configuration
  already grants (ADR-0002, KAN-987's descendant policy mechanism).
- **satay-runtime** (a co-evolving dependency, not a fixed one, as of
  2026-09-20). Previously treated as an external library this project
  tracked at a pinned version and worked around. The operator has since said
  satay's own roadmap should now be driven by cuttlefish-crew's needs -
  concrete asks are filed as satay-runtime issues, not worked around inside
  this repo. See Open risks.
- **Runners** (named here, not built until a later slice). A place that can
  run a project's deployment and expose it - the operator's own machine, a
  homelab box, or eventually cuttlefish-crew-provisioned compute. Out of
  scope for this milestone entirely; named so the backend interface this
  milestone builds doesn't accidentally foreclose it.

## Scope

**In this milestone.**

- A generic `AgentBackend` interface that a coding delegation runs through,
  replacing today's kopicode-hardcoded call site inside `cuttlefish.delegate`.
- `kopicode` reimplemented as one `AgentBackend` implementation, with zero
  behavior regression from V1/V2 - the same NDJSON parsing, the same policy
  file generation, the same sandbox routing, now living behind the interface
  instead of being the only thing that exists.
- A second `AgentBackend` implementation wrapping headless Claude Code,
  capable of running at least one real delegation end to end, proving the
  interface generalizes past kopicode's own shape.
- A backend-agnostic episodic event representation for a delegation outcome,
  forward-compatible with events V1/V2 already wrote (ADR-0004's
  unmarshalling discipline - an unrecognized or superseded event shape still
  round-trips, it isn't dropped).
- The external rebrand: repository name, README, CLI branding/help text, and
  docs cross-links read as **cuttlefish-crew**. The Python package import
  path (`cuttlefish`, `src/cuttlefish`, `pyproject.toml`'s `name =
  "cuttlefish"`) is unchanged (Q30).
- Superseding or amending ADRs for ADR-0002 (the product-ambition trigger it
  named has now fired) and ADR-0003 ("no new protocol, wrap kopicode as it
  exists" - now extended to "no new protocol *between* backends either," but
  the single-protocol assumption itself is superseded), recorded rather than
  left silently stale.
- **Slice B, project/agent-scoped secrets management (ADR-0006, Q34):** an
  encrypted-at-rest secrets store (`cuttlefish.secrets.SecretsStore`,
  `.cuttlefish/secrets.db`), scoped per project with an explicit shared
  scope (`SHARED_SCOPE`) for a value like a personal OpenRouter key. Both
  `KopicodeBackend`/`ClaudeCodeBackend`'s own `_credential_envs` now resolve
  a name from the store before falling back to `os.environ`, and forward it
  into a sandbox (`SandboxSpec.envs`) or a direct-host subprocess (a new
  `env` parameter on `run_kopicode`/`run_claude_code`) alike - the seam
  named in Q34 as "the thing to replace, not bypass" now goes through the
  store first, ambient environment second, rather than only ever reading
  `os.environ`. An operator declares which named secrets a task may read via
  `cuttlefish run --project NAME --secret NAME` (repeatable), the same shape
  `--allow` already established; a declared name absent from both scopes is
  a config-time error (Q17's fail-closed posture), not a silent no-op. The
  episodic journal's redactor is seeded with the same resolved names so a
  secret that leaks back into a tool result still gets caught, and no
  decrypted value ever crosses a satay task boundary (ADR-0006's own
  "no satay-journaled plaintext" section). No credential-broker/proxy - that
  remains explicitly deferred (Q34), unattempted this slice.

- **Slice C's team-concurrency half (ADR-0007):** `cuttlefish.team.run_team`
  runs N named roles' delegations concurrently via `satay.gather`, all
  sharing one `task_id` (their own satay run id) rather than one satay run
  per role - `run_task`'s own `task_id`-is-the-run-id identity discipline
  (ADR-0001/Q6) has no hook for a `start_child`-spawned run to learn its own
  id before its first journal write, so a shared journal with a `role` tag
  on every event was the additive answer, not a second identity scheme.
  `cuttlefish run-team --role NAME:TASK_TEXT` (repeatable); `maybe_handover`
  gained a `role` filter so one role's own context bloat can't force
  another's window closed early. Verified live (2026-09-20) that this is
  real concurrency, not a declared-but-serial fan-out - and, separately,
  that two roles sharing one kopicode-backed `--root` collide on kopicode's
  own per-working-tree session lock, a real, named, accepted gap (Q44), not
  a bug this project fixed or worked around at the time (a sequential-dispatch
  fallback closed it later, once live use showed it was the common case, not
  an edge one - Q54, `docs/SLICES.md`'s "D1 live-usage fixes").
- **The satay-runtime dependency slice C's steering half needed (Q42):**
  satay had no way to expose its own control API to anything outside a
  `cuttlefish run` process without pulling in `satay dev`'s whole
  interactive-session model. Filed and built directly in satay-runtime
  (satay-runtime PR #101, ADR-0046 there) rather than worked around inside
  this repo, per the standing Q33 instruction - `satay.control.run_app`,
  shipped as satay `0.2.0` (satay-runtime PR #102).
- **Slice C's steering half (ADR-0008):** `cuttlefish run --steerable`/
  `run-team --steerable` open `satay.control.run_app` instead of a bare
  `run_app`, print a `base_url`/token, and publish a local pointer file
  (`.cuttlefish/steering/<task-id>.json`); `cuttlefish steer <task-id>
  "<message>" [--role NAME]` is a thin HTTP client reading that file and
  `POST`ing to satay's own control API. A steerable task/team role becomes a
  loop of delegation rounds - after each round's own outcome is journaled, a
  single, plain `satay.wait_for_event(SteeringMessage, ...)` poll decides
  whether to fold a queued message into one more round or finalize exactly
  as a non-steerable task always has. **Redirects at a round's boundary, not
  mid-flight** - real design research found neither backend's headless
  surface accepts input after it starts, and racing `wait_for_event` against
  an in-flight delegation via `satay.gather` is an unverified composition of
  satay's own primitives (`WorkflowParked` unwinds the whole workflow drive,
  not one `gather` member) - named honestly as this slice's real limit, not
  discovered as a surprise later. Verified live (2026-09-21) against the
  real kopicode binary: a message sent mid-round starts a fresh round with
  it folded into the prompt, and a role nobody steers finalizes untouched.

- **Slice D1 (ADR-0009): a formal `Project` entity, the fleet daemon, and a
  plain UI.** `cuttlefish.projects.ProjectStore` (`~/.cuttlefish/projects.db`)
  gives a project a stable identity (`id`, `name`, `root`, `secrets_scope`,
  `roles` - each a name plus a persistent persona, Q31) independent of any
  single project directory's own `.cuttlefish/`. `cuttlefish.fleet
  .FleetDaemon` (`cuttlefish serve`) launches and owns every registered
  project's team concurrently, in one process - one `asyncio.create_task`
  per project running `run_team` against its own `satay.control
  .run_app(data_dir=<root>/.satay)`, never a subprocess (Q48/Q50) - exposing
  a loopback-only FastAPI surface (`GET/POST /api/projects...`) a Svelte +
  TypeScript + Vite frontend (this repo's first non-Python build pipeline)
  talks to: a portfolio grid and a per-project detail view with a working
  steer-chat panel, no pixel art yet (D2's job). `cuttlefish.runtime`'s
  process-global `_runtime` becomes a `contextvars.ContextVar` so N
  projects' teams can each hold their own episodic store / backend /
  secrets store concurrently (Q49). This closes "many projects, many teams,
  concurrently" without satay-runtime's own multi-worker milestone (Q48).

- **Slice D2 (ADR-0009's own "D2 renders instead"): the pixel-art skin.**
  Each role renders as a small animated pixel-art cuttlefish
  (`frontend/src/lib/pixel/`), tinted and posed by its live `RoleStatus` -
  a plain CSS grid of colored cells (`PixelGrid.svelte`), no canvas or
  game-rendering library (Q51). `OfficeScene.svelte` (a small CSS room)
  renders every role's sprite in the project detail view, additive above
  the existing steer-chat cards; the portfolio's own cards render the same
  sprites at a smaller scale in place of D1's plain status-chip rows.
  `SpriteGallery.svelte` shows every status with no daemon connection
  needed - the fastest way to see the art. `cuttlefish.fleet.server
  .find_free_port` (Q52) makes `cuttlefish serve` auto-pick a free port
  instead of failing when its default is taken; `scripts/demo.sh`/`make
  demo` is the one-command way to run a daemon and the dashboard together,
  dev-playbook's own "runnable in one command" guidance.

**Out.**

- The runner/hosting abstraction and remote demo viewing (slice E, was D).
- The meetings-with-avatar feature. Explicitly deferred to last, after
  everything else in this roadmap, by the operator's own instruction.
- Any actual multi-tenancy, auth, or isolation-between-operators
  implementation. "Building toward a product" is a design constraint
  ADR-0002's 2026-09-20 addendum has to acknowledge, not something built in
  code yet.
- Any change to satay-runtime's own codebase *beyond a concrete, narrowly
  scoped ask* (slice C's own `satay.control.run_app`, Q42, is the first
  exception to slices A/B's "no satay changes needed" - filed and built
  there directly, per the standing Q33 instruction, not worked around here).
- A credential-broker/proxy (the agent never holds a raw secret at all, only
  a scoped local proxy does) - explicitly deferred by Q34's own reasoning,
  real future work once slice B's simpler direct-injection version's gaps
  are concretely felt, not attempted this slice (ADR-0006).
- The `Project` entity is now in scope, as slice D1 (ADR-0009, Q38's own
  deferral target). Not out any longer - see the D1 bullet above.

## Requirements

| ID | Requirement | Status |
|----|-------------|--------|
| R0 | A coding subtask is delegated through a generic `AgentBackend` interface, not a kopicode-hardcoded call site. | Core goal |
| R1 | kopicode continues to work exactly as it does today behind the new interface - same NDJSON parsing, same policy file generation, same sandbox routing - zero behavior regression from V1/V2. | Must-have |
| R2 | A second backend (headless Claude Code) is implemented against the same interface and completes at least one real delegation end to end. | Must-have |
| R3 | The episodic event schema represents a backend-agnostic delegation outcome, forward-compatible with events V1/V2 already wrote. | Must-have |
| R4 | The repo/product's external surface (README, CLI help text/branding, docs cross-links) reads as cuttlefish-crew; the Python package import path (`cuttlefish`) is unchanged. | Must-have |
| R5 | ADR-0002 and ADR-0003 have superseding or amending ADRs recorded reflecting the pluggable-backend and product-ambition decisions. | Must-have |
| R6 | Existing sandbox routing (`CUTTLEFISH_SANDBOX=container\|e2b\|none`) and the declared per-task policy mechanism work per-backend, not only for kopicode. | Must-have |
| R7 | A project/agent-scoped secrets store is encrypted at rest, scoped per project with an explicit shared scope, and an operator never configuring it gets today's exact V1/V2/slice-A behaviour unchanged. | Must-have (slice B) |
| R8 | A declared secret is injected into both a sandboxed and a direct-host delegation, for both backends, through the same `_credential_envs` seam each backend already had - not a bolted-on second channel. | Must-have (slice B) |
| R9 | A store-resolved secret value never becomes a satay-journaled task argument or return value, and the episodic journal's own redactor still catches it if it leaks back into a tool result. | Must-have (slice B) |
| R10 | Several named roles' delegations against one project run genuinely concurrently, verified live, not just declared as such. | Must-have (slice C) |
| R11 | Each role's own working-memory handover fires independently, undisturbed by another role sharing the same journal. | Must-have (slice C) |
| R12 | A human can send a message that redirects a still-running delegation's work, not only read its history afterward. | Delivered (slice C, steering half, ADR-0008) - redirects at a delegation round's boundary, not mid-flight |
| R13 | A `Project` has a stable identity (independent of any single directory's own `.cuttlefish/`) that a team's roles and their personas attach to. | Must-have (slice D1) |
| R14 | An operator can start, stop, and steer several registered projects' teams concurrently from one long-running process, without satay-runtime's own multi-worker milestone. | Must-have (slice D1) |
| R15 | A plain (non-game) UI renders every registered project's live role status and lets an operator steer a role's work, wired to the same API the eventual pixel-art view (D2) will render against. | Must-have (slice D1) |
| R16 | Each role renders as an animated sprite whose pose/tint reflects its live status, at both the zoomed-out (portfolio) and zoomed-in (project detail) scale, with no new rendering dependency. | Delivered (slice D2) |
| R17 | An operator can bring up a fleet daemon and the dashboard together with one command, on a machine that may already have something else bound to the daemon's default port. | Delivered (slice D2) |
| R18 | A daemon-started (dashboard) team can run the same declared shell commands a CLI-started one can, via a persisted per-project setting rather than a per-run flag it has no way to supply. | Delivered (D1 live-usage fixes, Q53) |
| R19 | A 2+-role kopicode-backed team sharing one project's `root` completes instead of failing closed on kopicode's own per-working-tree lock. | Delivered, interim fix (D1 live-usage fixes, Q54) |

## Shape

| Part | Mechanism | ADR |
|------|-----------|-----|
| S1 | `AgentBackend` protocol (invoke, parse its own native stream into one `DelegationOutcome`, declare/accept a policy file) replacing the kopicode-specific call inside `cuttlefish.delegate` | forthcoming, supersedes ADR-0003 |
| S2 | `KopicodeBackend` - today's delegation logic moved behind the interface, behavior preserved byte for byte | ADR-0003 (superseded), forthcoming |
| S3 | `ClaudeCodeBackend` - headless Claude Code wrapped the same way, proving the interface isn't kopicode-shaped by accident | forthcoming |
| S4 | Backend-agnostic episodic event types for a delegation outcome, versioned per ADR-0004's forward-compatible unmarshalling | ADR-0004 |
| S5 | External rebrand: repo name, README, CLI branding/help text, docs cross-links -> cuttlefish-crew; package import path (`cuttlefish`) unchanged | - |
| S6 | An addendum to ADR-0002 (the multi-tenant trigger it named has fired) and a superseding ADR-0005 for ADR-0003 (multi-backend delegation) | ADR-0002 addendum, ADR-0005 |
| S7 | `cuttlefish.secrets.SecretsStore` - an encrypted-at-rest, project-scoped key/value store (`.cuttlefish/secrets.db`), plus `cuttlefish secrets set/get/list/delete/generate-key` and `cuttlefish run --project/--secret` | ADR-0006 |
| S8 | Each `AgentBackend`'s own `_credential_envs`/`CREDENTIAL_ENV_VARS` resolve a name from the store before `os.environ`; `run_kopicode`/`run_claude_code` gain an `env` parameter so a direct-host delegation gets the same injection a sandboxed one already had | ADR-0006 |
| S9 | `cuttlefish.team.run_team` - N named roles' delegations fanned out via `satay.gather` under one shared `task_id`, every event tagged `role`; `cuttlefish run-team --role NAME:TASK_TEXT` | ADR-0007 |
| S10 | `maybe_handover(..., role=...)` - the same algorithm, filtered to one role's own tagged events | ADR-0007 |
| S11 | `satay.control.run_app` (satay-runtime PR #101/#102, satay `0.2.0`) - the control API composed into `run_app`'s own ergonomics, unblocking steering without a cuttlefish-side workaround | satay-runtime ADR-0046 |
| S12 | `SteeringMessage` (doubles as satay's own wire payload type and the journaled episodic event) plus `cuttlefish.steering`'s round-boundary poll loop in `run_task`/`run_team`; `cuttlefish run --steerable`/`run-team --steerable` (open `satay.control.run_app`, print `base_url`/token, publish `.cuttlefish/steering/<task-id>.json`) and `cuttlefish steer <task-id> "<message>" [--role NAME]` (an HTTP client over that pointer file) | ADR-0008 |
| S13 | `cuttlefish.projects.ProjectStore` (`~/.cuttlefish/projects.db`) - a `Project`'s stable identity (`id`, `name`, `root`, `secrets_scope`, `roles: list[RoleDefinition]`); `cuttlefish projects add\|list\|remove` | ADR-0009 |
| S14 | `cuttlefish.runtime`'s `_runtime` global becomes a `contextvars.ContextVar`, so N projects' teams hold independent `Runtime`s concurrently in one process | ADR-0009 |
| S15 | `cuttlefish.fleet.FleetDaemon` (`cuttlefish serve`) - one `asyncio.create_task` per registered project's team, each its own `satay.control.run_app(data_dir=<root>/.satay)`, no subprocess; a loopback-only FastAPI surface (`GET/POST /api/projects...`) for start/stop/steer/status | ADR-0009 |
| S16 | A Svelte + TypeScript + Vite frontend (`frontend/`) - a portfolio grid and a per-project detail/steer-chat view, plain UI wired to S15's API | ADR-0009 |
| S17 | `frontend/src/lib/pixel/` - a small hand-authored pixel-art sprite (`cuttlefish.ts`) rendered as a CSS grid (`PixelGrid.svelte`), wrapped by `RoleSprite.svelte`/`OfficeScene.svelte`/`SpriteGallery.svelte`; replaces D1's plain status chips in `ProjectCard`/adds to `ProjectDetail` | Q51 |
| S18 | `cuttlefish.fleet.server.find_free_port` - `cuttlefish serve` auto-picks a free port; `scripts/demo.sh`/`make demo` - one-command daemon+dashboard, dev-playbook compliance | Q52 |

## Affordances

**Non-UI.**

| Affordance | Kind | Wires to |
|------------|------|----------|
| `CUTTLEFISH_AGENT_BACKEND=kopicode\|claude-code` | Config | Selects which `AgentBackend` implementation the delegation task routes through, the same pattern `CUTTLEFISH_SANDBOX` already established |
| `cuttlefish run "<task>"` / `cuttlefish show <task-id>` | CLI commands | Unchanged in shape |
| `cuttlefish run-team --role NAME:TASK_TEXT` (repeatable) | CLI command | Runs N named roles concurrently against one `--root`/`--project`, sharing one `task_id` (ADR-0007) |
| `cuttlefish run --steerable` / `run-team --steerable` | CLI flags | Opens `satay.control.run_app` instead of a bare `run_app`, prints `base_url`/token, publishes `.cuttlefish/steering/<task-id>.json` (ADR-0008) |
| `cuttlefish steer <task-id> "<message>" [--role NAME]` | CLI command | Delivers one `SteeringMessage` to a still-running `--steerable` task/role - redirects at the next delegation round's boundary, not mid-flight (ADR-0008) |
| `CUTTLEFISH_SECRETS_KEY` | Config | Opt-in, mirroring `CUTTLEFISH_SANDBOX`'s posture - unset means no `SecretsStore` at all, every credential still resolved from `os.environ` |
| `cuttlefish run --project NAME --secret NAME` | CLI flags | Declares this task's secrets scope and which named secrets (beyond a backend's own ambient credential names) it may read (ADR-0006) |
| `cuttlefish secrets generate-key\|set\|get\|list\|delete` | CLI commands | Manages the store directly - the only way to actually populate it |
| `cuttlefish projects add\|list\|remove` | CLI commands | Manages the `Project` registry without the daemon running at all (ADR-0009) |
| `cuttlefish serve [--port N]` | CLI command | Starts the fleet daemon - launches/owns every registered project's team concurrently, prints its own loopback base URL/token; auto-picks the next free port if `N` (or the default) is taken (Q52) | ADR-0009 |
| `make demo` / `scripts/demo.sh` | Make target | Starts a fleet daemon and the dashboard's dev server together, one command, prints both URLs - the dev-playbook-compliant way to actually try this out locally |

**UI.**

| Affordance | Kind | Wires to |
|------------|------|----------|
| Portfolio grid (project cards, each role rendered as a small pixel-art sprite, start/stop) | Svelte view | `GET /api/projects`, `POST /api/projects/{id}/start\|stop` (ADR-0009); sprite rendering is client-side only (Q51) |
| Project detail view (an animated office scene per role, steer-chat panel, event tail) | Svelte view | `GET /api/projects/{id}/status`, `POST /api/projects/{id}/steer` (ADR-0009); sprite rendering is client-side only (Q51) |
| Sprite gallery (every `RoleStatus`, live-rendered) | Svelte view | No API - reachable from the connect screen or the portfolio topbar with no daemon connection |

## Implementation decisions

The interface's job is to normalize every backend's outcome to the one
`DelegationOutcome` shape this project already defines, not to invent a
shared wire format between backends. Each backend keeps speaking its own
native CLI language (kopicode's NDJSON stream over `run --print`, whatever
headless Claude Code's own streaming shape turns out to be) - this extends
ADR-0003's original "no new protocol" discipline rather than abandoning it:
there's still no new protocol invented *for* any given backend, there's just
now more than one backend cuttlefish-crew knows how to talk to.

Sandbox routing and the declared per-task policy mechanism already live one
layer above any specific backend (`cuttlefish.sandbox`, `cuttlefish.tasks.
delegate`). Making them work per-backend should be an additive parameter to
that existing plumbing, not a fork of it - a second sandbox backend was
already added this way in V2 (ADR-0002's 2026-08-26 addendum), and a second
agent backend should follow the identical shape: one more implementation of
an interface this project already owns.

Backend heterogeneity is real and shouldn't be hidden: kopicode's policy
gate (KAN-987's descendant) is purpose-built and mature; headless Claude
Code's own permission model won't necessarily map onto the same declared-
allowlist shape. The interface should surface that difference honestly
(e.g. a backend reports what containment/policy guarantees it can actually
make) rather than force every backend to pretend to the same guarantees
kopicode happens to provide.

## Testing approach

Same discipline V1/V2 already hold: test against real binaries, not mocks,
for whichever backends have a real credential and binary available in the
build environment. If headless Claude Code isn't available as a live,
credentialed binary in this build's environment the way E2B wasn't in V2,
say so plainly rather than asserting the path from unmocked-but-credential-
less tests - unit- and integration-test the backend against its documented
contract, and name the live end-to-end path as an open gap until it's
actually run, the same honesty V2's `CLAUDE.md` entry already models for
E2B.

## Assumed defaults

| ID | Assumed | Cost if wrong |
|----|---------|---------------|
| Q29 | The second backend proving pluggability is headless Claude Code, not a third tool. | Small - the interface doesn't care which second implementation proves it; swapping which tool goes second is additive. |
| Q30 | The Python package import path stays `cuttlefish` while the repo/product/CLI branding become cuttlefish-crew externally. | Small now; if this project is ever published as an installable library under its own name, an import-path/product-name mismatch could confuse a new contributor - accepted for now, revisit if that happens. |
| Q38 | A project's secrets scope was a plain string (`--project NAME`, defaulting to `--root`'s directory name); a formal `Project` entity now exists (slice D1, Q47). | Resolved - `Project.secrets_scope` defaults to `Project.name`, so the store's own `scope` column needed no migration, exactly as this row originally predicted. |
| Q44 | A team's roles all share one `--root`, so kopicode's own per-working-tree session lock means only one role's kopicode invocation can actually edit at a time in practice - real concurrent *editing* needs separate checkouts, not attempted this slice. | Resolved to "sequential fallback, not separate checkouts yet" (Q54) - a 2+-role kopicode-backed team no longer fails closed on the lock; it dispatches one-at-a-time instead. Real concurrent editing still needs separate checkouts per role, deliberately deferred until this fallback's own cost is felt. |
| Q47 | The `ProjectStore` registry path (`~/.cuttlefish/projects.db`) is hardcoded this slice, no env override. | Small - a `CUTTLEFISH_PROJECTS_HOME`-style override is additive if a concrete need (e.g. multiple isolated registries on one machine) shows up; not built ahead of one. |

## Open risks

- **Steering redirects at a delegation round's boundary, not mid-flight
  (ADR-0008).** A message sent while a real coding-agent invocation is
  running waits for that invocation's own natural end before it's ever
  seen - a bound that can be minutes for a real task, named honestly rather
  than discovered as a surprise. Closing it further needs either a backend
  gaining a genuine live-input surface (neither kopicode nor headless Claude
  Code has one today) or a verified, satay-runtime-provided way to race
  `wait_for_event` against an in-flight task (ADR-0008 found the naive
  `satay.gather` composition unverified) - real future work, not attempted
  speculatively ahead of either existing.
- **satay-runtime is no longer treated as a fixed external dependency, and
  slice C is the first time this actually happened.** satay-runtime#101
  (ADR-0046 there, `satay.control.run_app`) closed the concrete gap slice
  C's steering half hit - the control API had no way to compose into
  `run_app`'s own ergonomics - and shipped as satay `0.2.0`
  (satay-runtime#102). Two of the three asks named when this pivot started
  remain open: satay-runtime#98 (a live query primitive - partially already
  answered by the existing `ReadAPI`, per the research behind #101) and
  satay-runtime#100 (the Postgres/multi-worker milestone, still not needed -
  slice C's own team concurrency runs entirely within one process via
  `satay.gather`, no multi-worker capability required at all, and slice D1's
  fleet daemon extends this same finding to *many projects'* teams
  concurrently too, Q48). #99 (document/example external steering) is
  effectively superseded by #101's own worked example.
- **This pivot is bigger than slices A-D2 alone.** Slice E (the
  runner/hosting abstraction unifying "tunnel to an always-on machine" and
  "cuttlefish-crew provisions a hosted deployment itself"), and a deferred,
  explicitly-last slice F (agent-initiated meetings, TTS + avatar,
  facilitated through existing video-call infrastructure rather than built
  from scratch) are both real, discussed, and intended. Slice B (ADR-0006),
  slice C's team-concurrency half (ADR-0007) and steering half (ADR-0008),
  and slice D (both D1 and D2, ADR-0009) are all now done. Slice B was
  inserted after slice A was first scoped, once it became clear that
  multiple projects each needing distinct, isolated credentials is a
  slice-A-adjacent pain, not a slice-E-hosting-only one (Q34).
- **A daemon restart loses every in-memory running-team handle (ADR-0009).**
  `cuttlefish serve` driving N projects' teams as in-process `asyncio` tasks
  means killing the daemon necessarily ends every team it owns - there is no
  separate supervisor process underneath it yet. A project's own status view
  still renders correctly from its `.cuttlefish/episodic.db` after a
  restart, but a team that was genuinely mid-round when the daemon died
  looks identical to one merely still journaling slowly, from that state
  alone. Real process supervision (the daemon restarting without losing
  running teams) is named honestly as future work, not attempted this
  slice, the same "don't build ahead of a proven need" posture ADR-0002
  already holds elsewhere.
- **The product-ambition decision fires one of ADR-0002's two named
  triggers, not the one about spinning the sandbox out as its own
  product.** ADR-0002 named two independent triggers: whether the sandbox
  becomes a separate product (ungated - no second real consumer of the
  interface itself exists, so this stays internal), and whether real
  containment is *necessary* (gated on multi-tenant exposure or untrusted
  task input). The operator has now said cuttlefish-crew is being built
  toward a product for other operators, not only personal use - that's the
  second trigger firing for real, addressed in ADR-0002's 2026-09-20
  addendum. It doesn't add new containment work (V2 already built real
  sandboxing before this trigger fired, for a different reason); it means
  the "one operator, their own machine" trust-model framing ADR-0002 uses
  throughout stops being the operating assumption, and isolation *between*
  operators becomes a real design question for whichever slice builds
  multi-operator hosting (slice E).
- **Backend heterogeneity may surface a real capability gap, not just an
  interface-design question.** If headless Claude Code (or any future
  backend) can't make the same containment/policy guarantees kopicode's
  KAN-987 gate makes, R6 may not be satisfiable uniformly across backends -
  an honest per-backend capability report, not a forced uniform contract, is
  the planned answer, but this is unverified until a second backend is
  actually built against a real policy requirement.
