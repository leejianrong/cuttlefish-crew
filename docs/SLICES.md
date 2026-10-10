# cuttlefish-crew: Slices

Vertical increments. Each ends in something you can demonstrate. Slice 1 confronts
the riskiest unknown: whether a satay-workflow core loop can actually survive a
crash mid-delegation and resume correctly, since everything else in this plan rests
on that being true.

## V1: A durable, resumable delegation to kopicode

**Delivers:** R0, R1, R2 (partial - see below), R3, R4, R5, R6, R7

**Build plan**

1. Scaffold the package (`uv init`, ruff, mypy `--strict`, pytest, matching
   satay-runtime's own toolchain), pin `satay==0.1.0`.
2. Define the episodic event types: a tagged union, versioned, with a redactor for
   known secret values at write time (ADR-0004). Get this right first - everything
   else appends to it.
3. Write the `@satay.workflow` core loop and its `@satay.task` boundaries: one task
   per LLM call, one task for the kopicode delegation (ADR-0001).
4. Write the kopicode delegation task against kopicode's **current** headless
   behaviour: shell out to `kopicode run --print`, parse its NDJSON stream, handle
   its present-day unconditional refusal as a real, journaled failure (ADR-0003).
5. Write the working-memory handover: a token-budget check, one bounded LLM call
   over the recent episodic window at the threshold, written back as an episodic
   event (ADR-0004).
6. Write the CLI (`cuttlefish run "<task>"`, `cuttlefish show <task-id>`).
7. Write the crash-recovery test using satay's `FaultInjector`: kill the process
   after a chosen journal event, resume, assert the same terminal state and no
   duplicated delegation call.
8. kopicode board KAN-987 has shipped (2026-08-23, kopicode PR #109,
   `internal/permission.AllowlistPolicy` plus `run --print --policy-file`).
   Extend the delegation task to write a policy file (a `root` scoped to the
   task's scratch checkout, an `allow` list built from the configured
   allowlist) and pass `--policy-file`, demonstrating an actual file edit
   landing through it - this is the part of R2 and R6 that couldn't be proven
   before. Note: kopicode ADR-0011 decision 4 asks the invoking orchestrator
   to provide process/container containment for any policy-gated invocation;
   slice 1 does this without one, a deliberate, named exception - see
   ADR-0002's addendum and Q25.

**Demo:** `cuttlefish run "add a .gitignore entry for build artifacts"` against a
scratch checkout, kill the process mid-run with `kill -9`, run it again, watch it
resume and finish. `cuttlefish show <task-id>` prints the whole thing afterward:
what was asked, what was delegated, what kopicode did, in order, from the journal.

**Rests on assumptions:** Q9 (the CLI blocks rather than running as a daemon) - if
wrong, the demo still works, but a "fire and forget" story needs a second surface
later. Q18 (no clarifying-question loop) - if wrong, an ambiguous task just does
its best or fails, rather than pausing to ask, which is a real gap a real operator
will notice quickly.

### Test plan

#### End-to-end

- A real task submitted via the CLI reaches a terminal state and prints a JSON
  result.
- Killing the process mid-delegation and restarting resumes to the same terminal
  state without a second kopicode invocation for the same call.
- `cuttlefish show` on a completed task renders the full sequence of what
  happened, matching the episodic journal exactly.
- A task requiring an action kopicode currently refuses headless (before KAN-987
  lands) surfaces as a clear, journaled failure, not a hang or a silent no-op.
- Once KAN-987 lands: a task that edits a file inside the scratch checkout's
  allowlisted scope actually lands the edit.

#### Integration

- The delegation task's NDJSON parser handles a real `run --print` stream,
  including a mid-stream cancellation event.
- A secret value (a fake API key) placed in a tool result is absent from the
  written episodic journal file, byte for byte.
- The working-memory handover fires at the configured token threshold and the
  resulting summary event is itself readable from the journal.

#### Unit

- Each episodic event type round-trips through its serialisation.
- The redactor strips every declared secret pattern and nothing else.
- The delegation task's idempotency key is stable across a retry of the same
  logical call.

## V2: Real containment and a general policy

**Delivers:** the sandbox package (ADR-0002), a general (non-hardcoded) policy
mechanism for the kopicode delegation, replacing V1's fixed allowlist once there's
a second real policy to compare it against.

**Build plan**

1. Build `cuttlefish/sandbox`: the create/exec/snapshot/destroy interface, one
   E2B-backed implementation. A second, container-backed implementation was
   added against the same interface once it turned out E2B needed a live
   account this project didn't have yet, and kopicode's own contract permits
   container containment as well as a microVM's - see ADR-0002's 2026-08-26
   addendum and `docs/QUESTIONS.md` Q27.
2. Route the kopicode delegation through it instead of a bare scratch checkout.
   Landed against the container backend specifically (`CUTTLEFISH_SANDBOX=container`,
   opt-in - unconfigured still means V1's original direct-host behaviour, not a
   default this project widened quietly). The scratch checkout, the kopicode
   binary, and the policy file are bind-mounted in rather than copied, so a real
   edit lands on the host exactly where V1 always put it. Two real gaps only
   showed up running this live, not from reasoning about the design up front: a
   container doesn't inherit the host's environment, so kopicode's own
   model-provider credential has to be forwarded explicitly; and a bare base
   image (verified against a few candidates) typically ships no CA bundle at
   all, so an outbound HTTPS call fails TLS verification unless one is
   provided - fixed by reusing whatever CA bundle the docker daemon's own host
   already has, since anything that can `docker pull` already needs one.
3. Generalise V1's hardcoded allowlist into a declared, per-task policy, informed
   by whatever V1's fixed allowlist turned out to actually need. Landed as a
   repeatable `cuttlefish run --allow "<shell command>"` flag.

**Demo:** the same delegation from V1, now running inside a container sandbox
rather than a bare scratch checkout, with the policy declared per task rather
than fixed in code. (E2B remains the backend for the same demo once there's a
live account to run it against - the interface doesn't care which backend a
given task uses.)

**Rests on assumptions:** ADR-0002's trigger condition (multi-tenant exposure, or
task input the operator didn't author themselves) has actually occurred by the
time this slice is scheduled - if it hasn't, this slice is speculative work ahead
of a real need, the same trap kopicode's own ADR-0008 warns against.

### Test plan

#### End-to-end

- A delegation that would escape a bare scratch checkout (writes outside the
  intended directory, or opens an outbound network connection the task didn't
  need) is contained by the sandbox and doesn't touch the host.

#### Integration

- The sandbox interface's create/exec/snapshot/destroy cycle is exercised against
  a real E2B account in CI, gated behind a cost-bearing test tag the same way
  kopicode gates its own paid `make bench`.

#### Unit

- The policy's allow/deny decision is exercised against a table of declared
  policies and requests, independent of the sandbox itself.

## V3: cuttlefish-crew — a pluggable, multi-project fleet

V1 and V2 proved one durable, sandboxed delegation to kopicode. V3 is the
pivot: cuttlefish becomes cuttlefish-crew, a fleet manager running teams of
coding sub-agents across many projects at once. See `docs/PLAN.md` for the
full problem/solution and `docs/QUESTIONS.md` Q28 onward for the decisions
behind it. Slices A, B, C (both halves), D1, and D2 are built; slices E and
F are named and real but not yet planned in this file - each gets its own
build plan once the slice before it ships and the interface it needs
actually exists.

### Slice A: a pluggable agent backend, and the external rebrand

**Delivers:** `docs/PLAN.md`'s R0-R6.

**Build plan**

1. Define `cuttlefish.agents.AgentBackend` (a Protocol): invoke a delegation,
   parse the backend's own native output into one `DelegationOutcome`,
   declare/accept a policy file, report what containment/policy guarantees
   it can actually make (ADR-0005).
2. Move today's kopicode delegation logic behind it as `KopicodeBackend`,
   behavior preserved byte for byte: same NDJSON parsing, same KAN-987-
   descended policy file generation, same sandbox routing.
3. Generalize the episodic event schema to a backend-agnostic delegation
   outcome, forward-compatible with every event V1/V2 already wrote
   (ADR-0004's unmarshalling discipline, exercised for real).
4. Implement `ClaudeCodeBackend` against the same interface, wrapping
   headless Claude Code's own equivalent output mode.
5. Wire backend selection via `CUTTLEFISH_AGENT_BACKEND=kopicode|claude-code`,
   mirroring `CUTTLEFISH_SANDBOX`'s existing pattern.
6. External rebrand: repo README, CLI branding/help text, docs cross-links
   read as cuttlefish-crew. Python import path (`cuttlefish`) unchanged (Q30).
7. Record ADR-0005 (supersedes ADR-0003's single-backend assumption) and
   addenda to ADR-0001 (satay's steering primitive) and ADR-0002 (the
   multi-tenant trigger firing) reflecting the pivot's decisions.

**Demo:** the same `cuttlefish run "<task>"` delegation from V1/V2, run twice
against the same task with `CUTTLEFISH_AGENT_BACKEND` set to each of
`kopicode` and `claude-code` in turn, both landing a real edit through their
own real policy/sandbox path, both producing a journal readable through the
same `cuttlefish show` regardless of which backend ran it.

**Rests on assumptions:** Q29 (headless Claude Code is the specific second
backend chosen to prove pluggability) - if a live, credentialed Claude Code
CLI isn't available in this build's environment, the live end-to-end path is
named as an open gap the same way E2B's was in V2, not asserted from
unmocked-but-credential-less tests.

### Test plan

#### End-to-end

- The same task submitted via `CUTTLEFISH_AGENT_BACKEND=kopicode` and
  `CUTTLEFISH_AGENT_BACKEND=claude-code` both reach a terminal state and land
  a real edit, each through its own backend's real policy path.
- `cuttlefish show` on a task run under either backend renders a full,
  readable sequence with no backend-specific event type leaking into a
  human-facing summary meant to be backend-agnostic.

#### Integration

- Each backend's own native output stream (kopicode's NDJSON, headless
  Claude Code's own streaming shape) is parsed into the same
  `DelegationOutcome` shape by its own adapter.
- Existing sandbox routing (`CUTTLEFISH_SANDBOX=container|e2b|none`) and the
  declared per-task policy mechanism are exercised against both backends,
  not only kopicode.

#### Unit

- The `AgentBackend` Protocol is satisfied by both `KopicodeBackend` and
  `ClaudeCodeBackend` (a structural conformance test, not just type-checking).
- An event written by V1/V2's kopicode-specific schema still round-trips
  through the generalized backend-agnostic schema.

### Slice B: project/agent-scoped secrets management

**Delivers:** `docs/PLAN.md`'s R7-R9.

**Build plan**

1. `cuttlefish.secrets.SecretsStore` (ADR-0006): an encrypted-at-rest,
   project-scoped key/value store over its own SQLite file
   (`.cuttlefish/secrets.db`), Fernet-encrypted under an operator-held
   `CUTTLEFISH_SECRETS_KEY`. `resolve(project, names)` checks a project's
   own scope first, falling back to the shared scope (`SHARED_SCOPE`).
2. Extend `AgentBackend` (`cuttlefish.agents.backend`) with
   `CREDENTIAL_ENV_VARS` (each backend's own always-relevant credential
   names) and a `secrets: Mapping[str, str]` parameter on `delegate()`.
   `KopicodeBackend`/`ClaudeCodeBackend`'s own `_credential_envs` now prefer
   a resolved secret over `os.environ` for the same name, falling back to
   `os.environ` when the store has nothing — the exact seam Q34 named,
   replaced rather than bypassed.
3. Give the direct-host path parity with the sandboxed one:
   `run_kopicode`/`run_claude_code` gain an `env: Mapping[str, str] | None`
   parameter, merged as `{**os.environ, **env}` via the shared
   `cuttlefish.delegate.subprocess_env.merge_env` (`None`/empty still means
   exactly today's full inheritance).
4. Wire `project`/`secret_names` through `cuttlefish.tasks.delegate
   .delegate_to_agent_backend` (resolution happens *inside* this
   already-`side_effect=True` task — a decrypted value is a local variable
   here, never a satay task argument or return value) and
   `cuttlefish.workflow.run_task`'s `TaskInput`; record `project`/
   `secret_names` (names only) on `DelegationStarted`.
5. `cuttlefish run --project NAME --secret NAME` (repeatable), mirroring
   `--allow`'s shape; a declared name absent from both scopes is a
   config-time error (Q17), checked before the workflow starts. Seed the
   episodic journal's `Redactor` with the same resolved names so a leaked
   secret is still caught (a store-resolved value never touches
   `os.environ`, the redactor's own default lookup).
6. `cuttlefish secrets generate-key|set|get|list|delete` for managing the
   store directly — `set`'s value is prompted (hidden) or read from stdin,
   never a command-line argument.

**Demo:** `cuttlefish secrets generate-key`, then `cuttlefish secrets set
--project demo HUGGINGFACE_TOKEN` (piped or prompted), then `cuttlefish run
"<task>" --project demo --secret HUGGINGFACE_TOKEN` — the delegation's own
sandbox/subprocess carries `HUGGINGFACE_TOKEN` without it ever being
exported into the operator's shell. Declaring the same `--secret` with
`CUTTLEFISH_SECRETS_KEY` unset, or a name that was never set in either
scope, both fail closed with a clear config error before any task starts.

**Rests on assumptions:** Q38 (a project is a plain string, not a formal
entity yet) — if wrong, only the CLI's `--project` surface and the store's
own `scope` column meaning need to change; the store's schema doesn't.

### Test plan

#### End-to-end

- `cuttlefish secrets set` then `cuttlefish secrets get` round-trips a
  value for a project scope and, separately, the shared scope.
- `cuttlefish run --project X --secret NAME` with `NAME` set only in the
  shared scope still resolves it (fallback), and a project-scoped value of
  the same name wins over a shared one when both exist.
- `cuttlefish run --secret NAME` with `CUTTLEFISH_SECRETS_KEY` unset, or
  with `NAME` absent from both scopes, both exit with `EXIT_CONFIG_ERROR`
  before a workflow starts.

#### Integration

- `delegate_to_agent_backend` resolves `secret_names` unioned with the
  configured backend's own `CREDENTIAL_ENV_VARS`, scoped to the declared
  `project` — verified against a real `SecretsStore`, not a mock.
- A `DelegationStarted` event written before this slice (no `project`/
  `secret_names` keys in its encoded data) still decodes, defaulting to
  the one scope every task implicitly ran under then.

#### Unit

- `SecretsStore.set`/`get`/`delete`/`list_names`/`resolve` round-trip
  correctly, including scope fallback and cross-project isolation; the
  on-disk ciphertext never contains the plaintext.
- Each backend's `_credential_envs` prefers a resolved secret over
  `os.environ`, falls back to it when absent, and forwards any other
  declared secret verbatim.
- `merge_env(None)`/`merge_env({})` both return `None` (full inheritance
  preserved); a non-empty mapping merges over a copy of `os.environ`,
  overriding a same-named ambient value.

### Slice C: multi-agent team concurrency and steerable chat (both done)

**Delivers:** `docs/PLAN.md`'s R10-R12.

**Build plan (team-concurrency half, done)**

1. `cuttlefish.team.run_team` (ADR-0007): N named roles' delegations, run
   concurrently via `satay.gather(..., return_exceptions=True)`, sharing one
   `task_id` rather than one satay run per role — `start_child` can't hand a
   parent a child's run id before that child's first journal write, so a
   second identity scheme was rejected in favour of a `role` tag on every
   event a role writes.
2. `role: str | None = None` added to `TaskSubmitted`, `DelegationStarted`,
   `DelegationCompleted`, `DelegationRefused`, `DelegationFailed`,
   `HandoverWritten`, `TaskCompleted`, `TaskFailed` — defaulting to `None`,
   so every event written before this slice (and every plain `cuttlefish
   run` after it) decodes and behaves unchanged.
3. `cuttlefish.handover.maybe_handover` gained a `role` filter: the same
   algorithm, narrowed to one role's own tagged events (including `None`)
   so one role's journal can't force another's window closed early, or
   suppress its next handover.
4. `cuttlefish run-team --role NAME:TASK_TEXT` (repeatable, at least one
   required); `--root`/`--project`/`--allow`/`--secret`/`--token-budget`
   apply to every role uniformly this slice (no per-role policy
   differentiation yet).
5. `cuttlefish.cli`'s config resolution (backend/LLM/sandbox/secrets/
   redactor) factored into `_prepare_run`/`_PreparedRun`, shared by `run`
   and `run-team` rather than duplicated.

**Demo:** `cuttlefish run-team --role builder:"add a .gitignore entry"
--role reviewer:"check the .gitignore entry is correct"` — both roles'
delegations start together, journaled under one `task_id` with `role` on
every event; `cuttlefish show <task_id>` renders both interleaved.

**Verified live, 2026-09-20:**
- Real concurrency, not a declared-but-serial fan-out: two roles pointed at
  a deliberately-slow fake backend binary completed in one sleep's
  duration, not two.
- A real, accepted gap: two kopicode-backed roles sharing one `--root`
  collide on kopicode's own per-working-tree session lock — the second
  role's kopicode process starts concurrently, then immediately refuses
  ("another kopicode session is already running in this working tree").
  Not a bug to route around (Q44) — real concurrent *editing* needs
  separate checkouts per role, not attempted this slice.

**Rests on assumptions:** Q43 (real concurrency, not just a per-role
mechanism, was the right scope for this slice) and Q44 (naming the
kopicode-lock gap rather than building worktree isolation to close it) —
if wrong, a later slice needs to add per-role root/checkout support before
"a team" is useful against kopicode for anything that actually edits files.

### Test plan (team-concurrency half)

#### End-to-end

- `cuttlefish run-team` with two roles reaches a terminal state and prints
  a JSON result naming both roles' own outcomes.
- `cuttlefish show <task_id>` on a team renders every role's events,
  distinguishable by their own `role` field.
- No `--role` at all, or a duplicate role name, both exit
  `EXIT_CONFIG_ERROR` before any workflow starts.

#### Integration

- A real `run_team` execution (a deliberately-missing kopicode binary, the
  same no-mock discipline `test_delegate.py`/`test_workflow.py` already
  hold) journals every event under the failing role's own `role`, and a
  collected `satay.TaskFailedError` unwraps to the same reason string a
  direct `DelegationError` would have produced outside a team.
- Each role gets its own `HandoverWritten` once its own window crosses
  budget, independent of the other role's.

#### Unit

- `maybe_handover(..., role=...)` filters strictly to that role's own
  events (including `None`, the plain single-task case); one role's
  handover never suppresses another's.
- `_parse_roles` splits `NAME:TASK_TEXT` on the first `:`, rejects a
  missing `:`, an empty name/text, and a duplicate name.

### Slice C: steerable chat (done)

**Build plan**

1. satay `0.2.0` (`satay.control.run_app`, satay-runtime PR #101/#102,
   ADR-0046 there) closed the concrete gap that blocked this — `cuttlefish
   run` had no way to expose satay's own control API to anything outside the
   process (Q42). cuttlefish's own pin bumped to `satay[studio]==0.2.0` (the
   `[studio]` extra pulls in the FastAPI/uvicorn `satay.control.run_app`
   itself needs — ADR-0008's own consequence, not an opt-in extra of
   cuttlefish's own).
2. A design pass first (ADR-0008), not skipped: the original sketch ("race a
   short `wait_for_event` against normal delegation progress" via
   `satay.gather`) turned out to be an unverified composition of satay's own
   primitives — `wait_for_event`'s `WorkflowParked` unwinds the *whole*
   workflow drive (a `BaseException`, handled only at the outermost per-run
   loop), not one `gather` member, and neither kopicode's nor headless
   Claude Code's headless surface accepts input after it starts anyway
   (verified from source — a single argv positional, no stdin wiring).
   ADR-0008 designed the buildable alternative instead: redirect at the
   boundary between delegation rounds.
3. `SteeringMessage(text, role)` (`cuttlefish.episodic.events`) — one
   dataclass doing two jobs: satay's own wire payload type
   (`wait_for_event`/`send_event` derive their inbox key's type name from
   its `module.qualname`) and the episodic event journaled the moment a
   round consumes one.
4. `run_task`/`run_team` gain `steerable: bool = False` (default off, byte-
   for-byte unchanged when unset) and become a loop of rounds: after each
   round's outcome is journaled, one plain, sequential `wait_for_event(...,
   timeout=DEFAULT_STEERING_GRACE_SECONDS)` decides whether to fold a queued
   message into one more round (`cuttlefish.steering.compose_steered_text`)
   or finalize with that round's own outcome. `run_team`'s own poll happens
   *after* its `satay.gather` resolves, sequentially per role, never nested
   inside one of that gather's members (the same unverified-composition risk
   step 2 found). A `DelegationError` (an infra-level failure, not a
   recorded `DelegationOutcome`) is never steered around, either workflow.
5. `cuttlefish.steering`: the key scheme (`task_id`, or `task_id:role` for a
   team role), the pointer file (`.cuttlefish/steering/<task-id>.json`,
   written by `run --steerable`/`run-team --steerable`, removed on exit),
   and `send_steering_message` — a synchronous HTTP client (`urllib.request`,
   no new dependency) POSTing to satay's own `POST /runs/{run_id}/events`.
6. `cuttlefish run --steerable` / `run-team --steerable` open
   `satay.control.run_app()` instead of a bare `satay.run_app()`, print
   `{"task_id": ..., "steering": {"base_url": ..., "token": ...}}`, and
   write the pointer file. `cuttlefish steer <task-id> "<message>" [--role
   NAME]` reads it and delivers.

**Demo:** `cuttlefish run --steerable "add a .gitignore entry"` in one
terminal; `cuttlefish steer <task-id> "actually add a .dockerignore
instead"` in another while the first round is still running — the task
starts a second round with the message folded into its prompt, visible in
`cuttlefish show <task-id>` as a `SteeringMessage` event between two
`DelegationStarted` events.

**Verified live, 2026-09-21**, against the real kopicode binary (a real,
rejected-but-uncharged network round trip so kopicode reaches a genuine
`session_ended` rather than failing before ever opening a session): a
message sent mid-round starts a fresh round with it folded into the prompt
for both a plain task and a team role; a role nobody steers finalizes after
exactly one round, untouched by another role being steered.

**Rests on assumptions:** the round-boundary redirect (not instant, bounded
by however long the round in flight takes) satisfies R12's "can redirect a
still-running delegation's work" as written — if an operator's real usage
needs faster-than-round-boundary responsiveness, that's new information this
slice's own design pass didn't have, not a bug in it.

### Slice D1: a `Project` entity and the fleet daemon (in progress)

**Delivers:** `docs/PLAN.md`'s R13-R15.

**Build plan**

1. `cuttlefish.projects.ProjectStore` (ADR-0009): a new SQLite store at
   `~/.cuttlefish/projects.db`, outside any single project's own
   `.cuttlefish/` (that directory's own layout - `secrets.db`, `episodic.db`,
   `.satay/` - is unchanged). `Project(id, name, root, secrets_scope,
   roles: list[RoleDefinition])`; `RoleDefinition(name, persona)` stores a
   role's durable voice/personality (Q31), never task text.
   `register`/`list`/`get`/`update_roles`/`deregister` (deregister only
   removes the registry row, never touches `root`).
2. `cuttlefish.runtime`: `_runtime` becomes a
   `contextvars.ContextVar[Runtime | None]` (Q49) - `configure` -> `.set`,
   `current` -> `.get`. Behaviourally unchanged for a single `cuttlefish
   run`/`run-team` process calling `configure()` once at startup.
3. `cuttlefish.fleet.FleetDaemon` (ADR-0009): `start(project, roles)`
   launches `asyncio.create_task` running `run_team` against
   `satay.control.run_app(data_dir=<project.root>/.satay)`, `steerable=True`
   unconditionally; registers the resulting `base_url`/`token`/`team_id` in
   an in-memory `dict[project_id, RunningTeam]` and updates
   `Project.last_team_id`. `stop(project)` calls satay's own already-built
   `POST /runs/{team_id}/cancel`. `steer(project, role, text)` calls
   `cuttlefish.steering.send_steering_message` directly (in-process, no
   subprocess to shell out to). `status(project)` opens that project's own
   `.cuttlefish/episodic.db` read-only and reduces each role's latest events
   to one of `queued`/`working`/`blocked`/`done`/`failed`.
4. A FastAPI app (no new dependency - `satay[studio]` already pulls in
   FastAPI/uvicorn) exposing `GET/POST /api/projects`, `POST
   /api/projects/{id}/start\|stop\|steer`, `GET /api/projects/{id}/status`.
   Loopback-only bind, a generated bearer token printed at startup
   (`x-cuttlefish-token`), the same posture satay's own control API holds.
5. `cuttlefish projects add\|list\|remove` (CLI, registry management without
   the daemon running) and `cuttlefish serve` (starts the daemon).
6. `frontend/` (ADR-0009): `npm create vite@latest -- --template svelte-ts`,
   a typed `fetch` API client against step 4's routes. A portfolio grid
   (`GET /api/projects`) and a project detail view (role cards, a steer
   textarea, an event tail) - plain UI, no pixel art (D2's job).
   `make frontend-check`/`make frontend-build` join `make ci`.

**Demo:** `cuttlefish projects add --root ~/code/some-project --role
builder:"ships fast, terse commit messages" --role reviewer:"skeptical,
flags risk before approving"`, then `cuttlefish serve` in one terminal,
then open the dashboard in a browser: the portfolio grid shows the
project; clicking "start" with a builder/reviewer task text launches both
roles concurrently (visible as `working` status chips within seconds);
opening the project detail view and sending a steer message to `builder`
redirects its next round, identically to `cuttlefish steer` today, but
from the browser.

**Rests on assumptions:** Q47 (`~/.cuttlefish/projects.db`, no env
override yet) and Q49/Q50 (an in-process `ContextVar`-scoped daemon rather
than a subprocess-per-project one) - if either needs revisiting, the fix is
additive (an env var; a subprocess fallback) rather than a rewrite, per
each question's own "cost if wrong."

### Test plan (D1)

#### End-to-end

- `cuttlefish projects add`/`list`/`remove` round-trip a project's identity
  and role personas through `~/.cuttlefish/projects.db`.
- `cuttlefish serve`, then starting two *different* registered projects'
  teams concurrently, both reach `working` status and both complete -
  verified live, not just declared, the same discipline Q43 already held
  for one project's own team concurrency.
- Steering a role through the daemon's `/steer` route redirects its next
  round, identically to `cuttlefish steer`'s own already-verified behaviour
  (ADR-0008).
- Stopping a running team via `/stop` results in that team's satay run
  reaching a cancelled/terminal state.

#### Integration

- `FleetDaemon.status` correctly derives `queued`/`working`/`blocked`/
  `done`/`failed` from a table of episodic-event sequences, independent of
  a live daemon.
- Two concurrently-running `FleetDaemon`-launched teams write to two
  *different* `.cuttlefish/episodic.db` files without cross-contamination -
  the concrete regression the `ContextVar` fix (Q49) exists to prevent.
- The FastAPI surface rejects a request missing `x-cuttlefish-token` or
  bearing the wrong one.

#### Unit

- `ProjectStore` round-trips `register`/`get`/`update_roles`/`deregister`;
  `deregister` never touches `root`'s own files.
- `runtime.configure`/`current` behave identically to the pre-ADR-0009
  global for one linear (non-concurrent) call sequence; two concurrent
  `asyncio.create_task`s each calling `configure()` see only their own
  `Runtime` from `current()`, never a sibling's.
- A role name supplied to `start()` that isn't in the project's registered
  `roles` runs with no persona prefix, not a rejected request.

### Slice D2: the pixel-art skin (done)

**Delivers:** the vision memory's "sub-agents as animated sprites/avatars
whose state reflects real status" (`docs/PLAN.md`'s Out-of-scope note on
D1 naming this as D2's job).

**Build plan**

1. `frontend/src/lib/pixel/cuttlefish.ts` (Q51): a small pixel-art
   cuttlefish mascot authored as row-strings (ASCII-art, proofreadable as
   text) - two frames (`a`/`b`, tentacles alternating) and a status-tint
   palette (`paletteFor`) mapping each `RoleStatus` to a distinct head/
   tentacle color, eyes/pupils constant across every status. `STATUS_GLYPH`
   is a small overlay glyph per status (`z`/`!`/`✓`/`×`; `null` for
   `working`, since the wiggle animation itself already signals activity).
2. `frontend/src/lib/pixel/PixelGrid.svelte`: renders a frame as a plain
   CSS grid of colored cells - no canvas, no game library (Q51).
3. `RoleSprite.svelte`: `PixelGrid` + the glyph overlay + a `working`-only
   frame-swap animation (`setInterval`, ~450ms) that respects
   `prefers-reduced-motion` (dev-playbook's own "quality floor" guidance).
4. `OfficeScene.svelte`: a small CSS-only room (wall/floor) holding one
   `RoleSprite` per role, wired into `ProjectDetail` above the existing
   steer-chat cards (additive - the cards, and `StatusChip`, are unchanged).
5. `ProjectCard.svelte` (the portfolio's zoomed-out view): the plain
   per-role `StatusChip` row replaced with small `RoleSprite`s - the same
   character, smaller, satisfying "don't render every sprite across every
   project on one screen" (the vision memory's own reasoning for needing a
   portfolio view at all) while keeping one consistent visual language
   between the zoomed-out and zoomed-in layers.
6. `SpriteGallery.svelte`: every `RoleStatus` rendered side by side,
   reachable from the connect screen and the portfolio topbar with **no
   daemon connection needed** - the fastest way to see the art actually
   render.
7. `frontend/src/lib/pixel/cuttlefish.test.ts` (vitest, new - `npm run
   test`/`make frontend-test`): the pure data layer (frame shape, palette
   completeness/distinctness, glyph invariants), independent of rendering.
8. `cuttlefish.fleet.server.find_free_port` (Q52) - `cuttlefish serve`
   auto-picks the next free port instead of failing when its default is
   taken, and `scripts/demo.sh`/`make demo` - the one-command way to run a
   daemon and the dashboard together and see this slice, dev-playbook's own
   "runnable in one command" guidance, verified live (a real terminal
   Ctrl-C cleanly stops the whole process group; a deliberately-occupied
   default port is skipped and the next one is used, printed correctly).

**Demo:** `make demo` - starts a fleet daemon and the dashboard together,
prints the URL/token to paste in. `cuttlefish projects add`/registering a
project through the UI, then starting a team, shows each role as an
animated cuttlefish in `OfficeScene`, tinted and posed by its live status;
the portfolio view shows the same characters, smaller, per project card.
`SpriteGallery` (linked from both the connect screen and the portfolio) is
reachable with no daemon at all.

**Verified live, 2026-09-22**: screenshotted (Playwright, headless
Chromium, both color schemes) against a real running daemon with a
deliberately-slow fake backend - `working` sprites visibly alternate
frames over consecutive screenshots; `ProjectDetail` correctly falls back
to the "start a team" form once a team finalizes; the small (portfolio-
scale) rendering stays legible.

**Rests on assumptions:** Q51 (no game/rendering library - revisit only if
a future need genuinely requires many more simultaneously-animating
sprites than a handful of small DOM nodes can hold).

### Test plan (D2)

#### Unit

- `cuttlefish.ts`'s frame pair is one consistent rectangular shape; every
  character either frame uses has a defined color in every status's
  palette; every status has a distinct head tint; eyes/pupils stay
  constant across every status; every non-`working` status has exactly one
  glyph, `working` has none.

#### Manual / visual (no headless-DOM test harness added this slice)

- Playwright screenshots against a live `npm run dev` + a real
  `cuttlefish serve`, both color schemes, at gallery scale and portfolio
  scale - the actual verification this slice's own PR was built against
  (component-level DOM testing, e.g. `@testing-library/svelte`, was not
  added; `cuttlefish.ts`'s own unit tests cover the part most likely to
  silently regress).

### D1 live-usage fixes: daemon allowlist + kopicode-lock sequential fallback (done)

**Delivers:** two real gaps a live 2-role dashboard run against a real repo
surfaced (`docs/QUESTIONS.md` Q53, Q54) - neither is a new capability, both
close a gap D1/D2 left open once actually exercised end to end.

**Build plan**

1. `Project.allow`/`ProjectStore` (Q53): a persisted `allow: tuple[tuple[str,
   ...], ...]` field, same shape and reasoning as `persona` (Q31) - reviewed
   once per project, not retyped per `cuttlefish serve` start. A migration
   guarded by `PRAGMA table_info` adds `allow_json` to an operator's existing,
   pre-this-slice `~/.cuttlefish/projects.db` in place, rather than requiring
   a fresh registry.
2. `cuttlefish projects add --allow CMD` (repeatable, reusing `_parse_allow`'s
   own `shlex.split` convention) and `_project_dict`'s JSON output carry it.
3. `cuttlefish.fleet.server`: `POST /api/projects` accepts `allow`, a new
   `PATCH /api/projects/{id}/allow` mirrors the existing `.../roles` route,
   and `_project_json` reports it.
4. `cuttlefish.fleet.daemon._build_role_inputs` (extracted from `start()` for
   direct unit testing): every role's `RoleInput` now carries the project's
   declared `allow`, team-wide - the actual fix for the `DelegationRefused`
   Q53 names.
5. `RegisterProjectForm.svelte` gains an "Allowed shell commands" textarea
   (one command per line, whitespace-split - a deliberate simplification
   over the CLI's own shell-quote-aware `shlex.split`, named in the
   component itself) and `FleetClient.updateAllow`/`registerProject`'s input
   type carry `allow`.
6. `cuttlefish.team._needs_sequential_dispatch`/`_dispatch_round` (Q54):
   every role in a team already shares one `root`, so two or more active
   kopicode-backed roles in a round dispatch one-at-a-time instead of via
   `satay.gather` - a hand-rolled mirror of `gather(...,
   return_exceptions=True)`'s own collect-mode contract, so one role's
   failure still doesn't stop the round. Any other backend, or a single
   active role, is unaffected.

**Demo:** `cuttlefish projects add --name demo --root <repo> --allow 'uv run
pytest' --role builder:...` then `cuttlefish serve` - a dashboard-started
team's builder role can now actually run the command the project declared,
where it previously always got `DelegationRefused`. A 2+-role kopicode-backed
team against one real repo now finishes instead of the second role failing
on kopicode's own "another kopicode session is already running" lock error.

**Verified live, 2026-09-22**: `cuttlefish projects add --allow`/`list`
round-tripped against a real (throwaway) `~/.cuttlefish/projects.db`; a
hand-built legacy `projects.db` (the exact pre-migration schema, `allow_json`
column absent) opened cleanly through `ProjectStore.open` with `allow`
correctly defaulting to `()`; the full suite including
`tests/integration/test_team_steering.py`'s real-kopicode two-role,
same-`root` steering test (`requires_kopicode`) passes with the sequential
fallback engaged, and `make ci`'s full scope (backend + `frontend-check`/
`frontend-test`/`frontend-build`) is green.

**Rests on assumptions:** the interim, not the real, fix for Q54 - separate
git worktrees per role stays deferred until this fallback's own cost is felt
(ADR-0002). Secrets-store access for daemon-started teams and the dashboard
not surfacing `last_team_id` are named, not built, this slice (D1D2
live-usage findings memory).

### Test plan (D1 live-usage fixes)

#### Unit

- `ProjectStore`: `allow` round-trips through `register`/`get`, `update_allow`
  replaces the whole set, a legacy (pre-`allow_json`) on-disk database
  migrates in place on open.
- `FleetDaemon._build_role_inputs`: every role's `RoleInput` carries the
  project's declared `allow` team-wide, alongside the existing persona-prefix
  behavior.
- `cuttlefish.team._needs_sequential_dispatch`: true only for kopicode with
  more than one active role; false for any other backend or a lone role.

#### Integration/e2e

- `cuttlefish projects add --allow`/`add` CLI round trip.
- Fleet HTTP surface: register-with-`allow` then get round-trips it,
  `PATCH .../allow` replaces the whole set, an unknown project id is 404.
- `run_team`'s existing missing-kopicode-binary tests (two roles, default
  `agent_backend`) already exercise the sequential-dispatch branch on every
  run, order-agnostic by construction.
- `test_team_steering.py`'s real-kopicode two-role test (`requires_kopicode`)
  is the one live, non-mocked verification that the sequential fallback
  actually avoids the lock collision it was built for.

### Slices E, F: not yet fully planned

Named and real, sketched in `docs/PLAN.md`'s Open risks and
`docs/QUESTIONS.md` Q28-Q52, but neither has its own build plan yet.

- **Slice E - runners and hosting**: a registered-runner abstraction (an
  always-on operator machine, or a cuttlefish-crew-provisioned deployment)
  fronted by a stable URL, closing the "view a real demo without being at
  the machine" gap. Needs satay-runtime's Postgres/multi-worker milestone
  (satay-runtime#100) only if it ever needs to run more workflow *engines*
  than one host process can hold - D1's own finding (Q48) means this isn't
  needed purely for "many projects concurrently."
- **Slice F - meetings, explicitly last**: agent-requested or on-demand
  meetings, TTS + an avatar presenting project status over existing
  video-call infrastructure cuttlefish-crew facilitates rather than builds.

## V4: Usable out of the box, and live prompts

**Why:** operator feedback (2026-10-06): agents can't run any command by default, a
denied permission can't be granted from the dashboard, the register form is hard to use,
and every team needs a hand-written prompt. Target design:
[`docs/design/ui-redesign/`](design/ui-redesign/README.md). Frontend slices use the
`frontend-design` and `material-design-3` guidance and the tokens in that folder's
`m3.css`.

**Decisions made:** `git commit -m '<msg>'` is allowed through a narrow quote-aware
rule (not by loosening the plain-word-list match). Permission modes are Ask first,
Standard (default) and Auto; Auto keeps a hard never-allowed list (`sudo`, `rm -rf`
outside the root, `git push --force`, `curl ... | sh`, writes outside the root).
kopicode needs an `auto` consent mode first:
[kopicode#164](https://github.com/leejianrong/kopicode/issues/164). Live prompts for
Claude Code and Codex are real but unverified, so each gets a spike before its build.

**Build plan** (order is the dependency order; each is its own PR)

*Defaults (backend; unblocks agents today)*

1. **V4-A: default allow presets.** A `dev-default` preset (inspect, git read, git
   save-work, Python, JS/TS) applied when a project or role declares no `allow`; opt-in
   presets (Go/Rust, containers); declared entries add to the preset. The never-allowed
   list lives here as one shared constant. Quote-aware `git commit -m '<msg>'` rule in
   `delegate/consent.py`. Mapping to `--allowedTools` and `--sandbox` stays the
   documented approximation. Update `known-gaps.md`, README and `docs-site/`.
2. **V4-B: built-in roles and team templates.** Builder, reviewer, tester, planner,
   docs writer, each with a real prompt, a default permission preset (reviewer and
   planner read-only) and a suggested backend; templates Solo builder, Builder +
   reviewer (default), Full crew. A role's prompt reads "default" until edited, with
   reset. API and CLI (`projects add --template`).
3. **V4-C: permission modes.** The Ask first / Standard / Auto model, persisted per
   project with per-role override; API; mode-to-backend mapping (kopicode, Claude
   Code `--permission-mode`, Codex `--sandbox`) written down with its honest gaps. Auto
   for kopicode is wired when kopicode#164 lands; until then Auto is unavailable on that
   backend, stated plainly. ADR for the mode model.

*Dashboard (frontend; needs 1-3 for real data)*

4. **V4-D: theme and shell.** M3 tokens and the navigation rail from `m3.css`, existing
   screens re-skinned, no behaviour change. Light and dark derived from the one seed.
5. **V4-E: folder picker and the short register flow.** `GET /api/fs` (list
   directories under a configured base, git state, no symlink escape, no hidden paths)
   and the screen-1 flow, with Advanced collapsed.
6. **V4-F: Permissions tab and roles/teams editor.** Screens 3 and 4 on the V4-A to
   V4-C APIs.
7. **V4-G: UX review.** One sub-agent reviews the built screens against the mockup and
   against Claude Projects and Paperclip; findings become fixes before moving on.

*Live prompts (the "Needs you" inbox)*

8. **V4-H: pending-request model and kopicode live prompts.** A journaled pending
   request (permission, question, blocked action) with an answer API and a timeout that
   denies. kopicode: hold the `consent.request` open for Allow once / Always allow /
   Deny (Always writes a rule into the project's allow list); bubble kopicode's `ask`
   tool questions up. Screen 2. ADR for the request model.
   **Shipped in five parts (ADR-0028, #73 to #78):** H1 model and validation, H2 kopicode wiring,
   H3 HTTP API, H4 dashboard, H5 real-kopicode tests and docs. What moved: kopicode's `ask`
   questions are **not** bubbled up (no released kopicode has a wire for them; `ask.request` is on
   kopicode `main`), so a question shows in activity as one nobody could answer. The Ask-first
   wording moved from H3 to H4, shipped with the screen and with asking on by default. A request's
   role comes from the task text a role was dispatched, not a contextvar or task argument. Stop
   cancels a team's pending requests so a round held open for a person can end. Standard asks as
   well as Ask first, on kopicode only; `cuttlefish run` has no inbox.
9. **V4-I: blocked-action fallback.** For any backend without a live path: record
   what was blocked or asked, show it under Needs you labelled with when the answer
   lands, and "Allow and rerun round" / resume with the answer (`claude --resume`).
10. **V4-J: Claude Code spike (done 2026-10-10, `docs/research/claude-code-stream-json-spike.md`: permission requests, `AskUserQuestion`, interrupt and resume all work over stream-json with `--permission-prompt-tool stdio`).** Run a real `claude` under `--input-format stream-json`
    plus `canUseTool` or `--permission-prompt-tool`; record the actual permission and
    `AskUserQuestion` message shapes and the answer round trip in a research note. No
    product code.
11. **V4-K: Claude Code live prompts**, unblocked by V4-J, on the shapes it recorded.
12. **V4-L: Codex spike (done 2026-10-08, `docs/research/codex-app-server-spike.md`: approvals, interrupt, resume, usage all work over `app-server`).** Install `codex`, drive `codex app-server`, record the
    approval and user-input requests (note open Codex issues #14192 and #21982). No
    product code.
13. **V4-M: Codex live prompts** (done 2026-10-10): `codex app-server` is Codex's default transport, with command and edit approvals decided by the shared policy or put to a person; `exec` stays as `CUTTLEFISH_CODEX_TRANSPORT=exec`. Left for follow-ups: `requestUserInput`, the final message in the journal, thread resume, token and context limits.

**Demo:** register a folder with two clicks and a template; the builder runs tests and
commits unattended; the reviewer is read-only; a command off the list shows up under
Needs you and the answer reaches the agent.

**Rests on assumptions:** Claude Code's `canUseTool` carries `AskUserQuestion` answers
back to the model (documented only in part; V4-J settles it); Codex `app-server`
approvals are reliable enough to build on (V4-L settles it).


## V5: A project's environment is cuttlefish's job, and the log tells the whole story

**Why:** a real two-role team failed with `stop=max_turns` in 90 seconds and nothing said why
(2026-10-07). The agents ran cuttlefish's own `.venv` Python instead of the project's, and the
log held no failure. Decisions and evidence: [ADR-0029](adr/0029-a-project-environment-is-cuttlefishs-job-and-the-log-is-a-projection-of-the-journal.md),
Q56 to Q61. "E" below is for *environment*; it is not V3's "Slices E, F".

**Decisions made:** cuttlefish detects, prepares and activates the environment for every
backend; kopicode inherits it and needs nothing new. The child environment is built from an
allowlist. A project's first install needs one confirmation, then runs automatically when stale.
The log is a projection of the episodic journal (ADR-0004) plus named operational events, kept
in `~/.cuttlefish/logs/cuttlefish.log`.

**Build plan** (each is its own PR)

1. **V5-E0: stop the bleeding.** Log failed team starts with the cause; `make demo LOG=1`
   writes a log file; Ctrl-C stops `cuttlefish serve` cleanly. Then: log a failed delegation
   (reason, failure kind, role) and drop cuttlefish's own venv (`VIRTUAL_ENV`, `PATH` entries
   under its `sys.prefix`) from the child environment, with a test that pins today's `.env`
   inheritance until E4. Shipped as one PR.
2. **V5-E1: observability**, in two PRs.
   **E1a:** one logging setup (`CUTTLEFISH_LOG_LEVEL`, rotating
   `~/.cuttlefish/logs/cuttlefish.log`, project, team and role on every line through a
   `contextvars` object); the `EpisodicStore.append` projection with redaction;
   `DelegationFailed` gains `failure_kind` and `record`; daemon lifecycle, backend resolution
   and HTTP 4xx and 5xx logged with their detail.
   **E1b:** the dashboard shows the server's real failure reason; `cuttlefish doctor` (binaries
   and versions, credential names set, `PATH` leaks, the log file, each project).
   **E1c:** the failed-round UI: words for the failure, no Approve/Reject when nothing waits.
   `DelegationFailed.detail` (a redacted tail of the failing command's output) moves to V5-E5,
   which is the first slice that reads tool output.
3. **V5-E2: environment spec and detection (shipped).** A read-only `EnvironmentSpec` for Python, Node
   and Go from marker files and version hints; never executes anything. The read-only
   Environment card on the project screen, and `doctor` reports it. Rust, Java and Ruby are
   detected as "not prepared yet".
4. **V5-E3: preparation**, in two PRs.
   **E3a (shipped):** `uv sync` / `uv venv` + `uv pip install`, and `npm ci` / pnpm / yarn / bun, run by
   cuttlefish inside the team's own start, before the first round and outside any agent's turns. Staleness
   by fingerprint in `.cuttlefish/env.json`; journaled with the command, exit code, duration and a redacted
   tail; a failed install fails every role with why; a stop kills it. The confirmation is the project's
   `env_prepare` setting (`ask`/`auto`/`off`) and the start call's `prepare` (`yes`/`skip`), not a Needs-you
   request (see the ADR's update). API, MCP `start_project`, and the activity-log wording.
   **E3b (shipped):** the dashboard side: a confirm card on Start ("Install and start", "Always install", "Start
   without"), the setting, and the install as a visible step.
5. **V5-E4: activation (shipped).** The allowlisted base environment (one constant, never per backend),
   the project overlay (`VIRTUAL_ENV`, `.venv/bin`, `node_modules/.bin` first on `PATH`), the
   short environment note in every role's brief, and `/mnt/c` entries dropped from the child
   `PATH` unless a project opts in. `ServePool` is already keyed by environment.
6. **V5-E5: environment-stuck detector**, in two PRs. N consecutive environment-signature failures
   end the round and raise a Needs you request of kind `blocked` with the evidence, instead of running
   to `max_turns`. Signatures are data. The stream carries no command output, so the detector reads the
   session's own record (see the ADR's update).
   **E5a (shipped):** `cuttlefish.stuck` (signatures, `StuckDetector`, `SessionRecord`), the serve child cancels
   the session at N (`CUTTLEFISH_STUCK_THRESHOLD`, default 5, `0` off), the round fails with
   `failure_kind="environment_stuck"` and `DelegationFailed.detail` (a redacted tail of the failing output),
   words and a "Last failing command" block in the activity log. kopicode only.
   **E5b (shipped):** the `blocked` Needs-you request after such a round: raised by the delegation task, no answers
   and no deadline, "Takes effect next round", a "Stuck" card with the last failing command, ended (new resolution
   `superseded`) when the role is steered or decided on, or with the team. Same API and MCP `list_requests`.
7. **V5-ask (shipped, after E5): a live answer to a kopicode `ask` question.** kopicode v0.4.0 released
   `ask.request`; a `question` Needs-you card with a text box, `answer` (with `text`) and `decline`.
   See ADR-0028's update.
8. **V5-limits (shipped, after ask): long unattended runs.** `max_turns` 100 and a 5M token budget on
   `session.start`; a round that stops on either, unstuck, continues from the handover (`RoundContinued`),
   up to 20 times per role. ADR-0030. Next: a no-progress stop, a wall-clock limit per round, and ending
   a round on context pressure (`session.usage`). The no-progress stop and a 2-hour round limit shipped
   next (ADR-0030's update), then V5-context-pressure: a round ends when its context passes 75% of the
   model's window.
9. **V5-E6: more ecosystems and isolation**, in three PRs. Node version managers are not part of it.
   **E6a (shipped):** prepare poetry, pipenv, Go, Rust, Ruby and Java (see the ADR's update).
   **E6b (shipped):** monorepo subfolders: the root and the folders directly under it; each nested project has its
   own `path`, install, `env.json` key (`node:frontend`) and journal events.
   **E6c (shipped, with E6a):** a design note for a hermetic per-project container through the sandbox seam
   (ADR-0002). No product code for the container.

**Demo:** register a folder with its own `.venv`; the Environment card says what it found;
Start prepares it if stale; the builder runs the project's tests with the right interpreter;
a deliberately broken dependency ends the round under Needs you in seconds, and
`~/.cuttlefish/logs/cuttlefish.log` shows every step.

**Rests on assumptions:** `.env` values reach the agent's shell today (read from `merge_env`,
not yet checked end to end; V5-E0 adds the test); an install step for a typical project fits
inside a start the operator is willing to wait for.
