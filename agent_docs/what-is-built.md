# What's built

Moved out of `AGENTS.md` so the root brief stays short. This is a history of
shipped slices, kept for the *why* behind each; `git log --oneline`,
`docs/adr/` (0001-0021 and counting) and each module's own doc comment are
the authority, and anything here that disagrees with the code is stale.
Slices after KAN-1764 (for example ADR-0021, kopicode over `serve`) are in
`git log` and the ADRs, not below, except V4-H at the end.

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

V4-H (ADR-0028, #73 to #78): **Needs you**. A kopicode agent in Ask first or Standard that asks
for a command nothing approves is paused until a person answers, instead of being refused.
`consent.request` was already answered by an async decider under a deadline, so holding it open
needed no new transport: `AskingDecider` raises a request and waits. A request is two journal
events, `RequestRaised` and `RequestResolved`, and its state is folded from them; there is no
requests table (ADR-0004), and the broker's in-memory index only caches the pending ones, which
is correct because nothing can be pending after a restart (the daemon marks leftovers
`abandoned` on start). The broker reaches the delegation task through `Runtime`, never a task
argument (ADR-0006), and a request's role is found from the task text the team dispatched,
because `satay.gather` runs calls in satay's own tasks where a contextvar does not reach.
Always allow goes through the same `ConsentPolicy` check as a hand-typed command, so the
never-allowed list still wins, and it applies to the running team at once and is saved to the
project. Stop resolves the team's requests `cancelled`, because satay's cancel only lands when the
round ends and a round held open for a person does not end. The window is
`CUTTLEFISH_REQUEST_WINDOW` (default 10 minutes), passed to kopicode as `--consent-timeout` when
`serve --help` lists it (v0.3.0 and later), else 45 seconds under kopicode's fixed 60. Claude Code
and Codex cannot pause, so they still refuse; `ask` questions became live with kopicode v0.4.0
(see V5-ask below). Real-kopicode checks are in `tests/integration/delegate/`; the live-model ones need a key.

V5-E0 and E1 (ADR-0029, #89 to #91, plus the failed-round UI): **what the daemon says when something
goes wrong.** A real team failed with `stop=max_turns` in 90 seconds and nothing said why: the agents
ran cuttlefish's own venv Python (`uv run` puts it first on `PATH`), and no log recorded the failure.
Now `cuttlefish serve` always writes `~/.cuttlefish/logs/cuttlefish.log` (rotating, level from
`CUTTLEFISH_LOG_LEVEL`) with the project, team and role on every line (`cuttlefish.logsetup`, a
`contextvars` context). Lifecycle lines are a projection made inside `EpisodicStore.append` from the
already-redacted event, so there is still one transcript (ADR-0004); HTTP errors, team start and stop,
and the resolved backend are logged directly. `DelegationFailed` carries `failure_kind` and the backend's
own `record` directory. `merge_env` always returns an environment without cuttlefish's own venv, and every
spawn goes through it. `cuttlefish doctor` checks binaries, credential *names* (a placeholder
`CUTTLEFISH_SECRETS_KEY` is a failure), `PATH` leaks and each project. MCP tool errors are `ToolError`s, so a
client sees the daemon's reason. The dashboard shows a failed start's reason and, for a failed round,
words for the failure kind; a blocked role shows Approve and Reject only when something waits for a decision
(a review gate or a usage limit), otherwise a note that the task ends unless you steer. Found by driving the
dashboard, CLI and MCP with sub-agents (`agent_docs/exploratory-testing.md`). Not built yet: the rest of
V5 (environment detection, preparation, the allowlisted base environment, the stuck-agent detector).

V5-E2 (ADR-0029): **environment detection.** `cuttlefish.environment.detect(root)` reads marker files at the
project root (never subfolders) and returns an `EnvironmentSpec`: Python (uv, poetry, pipenv, pip), Node
(npm, pnpm, yarn, bun, from `packageManager` or the lockfile), Go, Rust, Java and Ruby, each with its
manifests, lockfile, the version it asks for (`.python-version`, `.nvmrc`, `.tool-versions`, `mise.toml`,
`engines`, `requires-python`, `go.mod`) and whether `.venv` or `node_modules` exists. It executes nothing, reads
no file over 1 MB and treats a malformed manifest as empty. Surfaces: `GET /api/projects/{id}/environment`
(read off the event loop), the dashboard's Environment card, `cuttlefish doctor`'s project lines and the MCP
`get_project_environment` tool. Installing is V5-E3; until then the card says agents start without
dependencies.

V5-E3a (ADR-0029): **cuttlefish installs dependencies before the team starts.** `cuttlefish.envprep` turns
the detected environment into steps (`uv sync [--frozen]`, `uv venv` + `uv pip install -r`, `npm ci`,
pnpm/yarn/bun frozen installs), decides what is stale from a fingerprint kept in `.cuttlefish/env.json`
(written only after a success; a person's own install is adopted and trusted until its files change), and
runs a step without a shell, from the project root, in `merge_env`'s environment, in its own process group,
under `CUTTLEFISH_PREPARE_TIMEOUT`. The daemon runs the steps inside `_drive`, after the satay control server
is up and before `satay.start`, so `start` returns at once; they are journaled as `EnvironmentPrepareStarted`
and `EnvironmentPrepared`. The confirmation is the project's `env_prepare` (`ask`/`auto`/`off`, a new
`projects.db` column read as `ask` when null) plus the start call's `prepare` (`yes`/`skip`); `ask` with a stale
environment and no `prepare` is `EnvironmentConfirmationError`, a 409. A failed install records every role
failed; a stop sets a cancel flag that kills the install (a team still installing has no satay run to cancel).
The dashboard side (confirm card, setting) is V5-E3b.
Found by driving real `uv`, `npm` and `pnpm` against dependency-free projects, and fixed before merge: an install that
failed after `uv venv` left a `.venv` that passed as installed, so the next start ran agents with nothing installed. A
failed or interrupted install is now recorded (`how: failed`) and reads as stale ("the last install did not finish") until
files change or it succeeds; a missing tool installed nothing, so it is not recorded. A successful install that made no
folder (no dependencies) is remembered as such (`produced: false`) so it is not reinstalled on every start. The install runs
with quiet, non-interactive settings (no update banners or progress bars, no corepack prompt), and a failed role's error
names the command and exit code, the last line of the output and `prepare=skip`.

V5-E3b: **the dashboard asks before it installs.** `start()` in `ProjectDetail` reads `GET .../environment` first: with the
setting "ask" and steps to run it shows `PrepareConfirm` (the commands, the folder, four choices) instead of calling start;
"automatically" saves the setting then starts with `prepare: "yes"`. The Environment card carries the setting (a radiogroup
over `PREPARE_SETTINGS`), `installProgress(events)` drives a status line while an install runs, and install rows show their
output under a collapsed "Output". The server's `409` stays the fallback for a client that raced a setting change.
Found by driving the install UI in a browser, fixed before merge: a retry after a failed `uv venv` + install stopped on "a virtual
environment already exists" (the retry now runs `uv venv --allow-existing`); the Environment card said `.venv is there` for a
half-made venv (a plan step for an installed ecosystem now shows its reason, "the last install did not finish", as a stale
row) and went stale after an install (it reads again when an install event or the setting changes); a disabled button dropped
keyboard focus (the setting uses `aria-disabled`); the confirm card now takes focus and Cancel returns it; a role-less log row
shifted its text into the narrow role column (the cell is always there); a failed install was labelled "Install done"; and the
failed role's text no longer quotes the last line of the output, which is often half a sentence.

V5-E4 (ADR-0029): **what an agent is given.** `merge_env(env, root=, passthrough=, tools=)` builds an allowlisted
environment (module doc lists it) in place of a copy of the daemon's: no `CUTTLEFISH_*`, no `.env` values, nothing `uv run`
set; `PATH` without cuttlefish's venv and WSL's `/mnt/...`; the project's `.venv` and `node_modules/.bin` first; credentials
last. Per-backend names (`KOPICODE_*`, `CLAUDE_*`, `CODEX_*`) and the operator's `CUTTLEFISH_AGENT_ENV_PASSTHROUGH` extend it;
installs also get the package tools' settings. The resident `kopicode serve` child is keyed by project root (it reads its
environment once). Each role's brief gets an `Environment:` note from `environment.brief`. `cuttlefish doctor` lists the
withheld names. The scripted fake agent logs the environment it sees, so tests assert what an agent really received.
Found by driving it with a daemon started by `uv run` and fake secrets, and fixed before merge: the DEBUG "withheld" line listed a
credential the backend had declared (and so was passed); the doctor line showed the first twelve names alphabetically, hiding
the ones a person wants (now credentials, SSH and cloud names come first, `doctor --all-env` lists everything, and it hints that
git over SSH needs `SSH_AUTH_SOCK`).

V5-E5a (ADR-0029): **an agent stuck on its environment is stopped.** The serve stream has no command output (only tool, exit
code, size; read from kopicode's `print.go`), so `cuttlefish.stuck.SessionRecord` reads the session's own
`<root>/.kopicode/sessions/<session>/events.jsonl` as it grows (cuttlefish picks the session id, so the path is known
mid-round; a spilled output is the tail of `<root>/.kopicode/blobs/<hash>`, the name checked to be hex). `StuckDetector` counts
consecutive `run_shell` failures whose output matches `SIGNATURES` (data); a success or a non-matching failure resets it, other
tools are ignored. `ServeChild` checks on each shell `tool_result`, serialised, off the loop; at N it records a verdict and
cancels the session, and `run_kopicode_serve` turns the result into a failed outcome with `failure_kind="environment_stuck"` and
`detail`. It fails open: no file, a half line, an unreadable blob or a sandboxed child (`process_factory`) means no evidence.
`DelegationFailed` and `DelegationOutcome` gained `detail`. The fake serve child has a `record` step to write that file.

V5-E5b (ADR-0029, ADR-0028): **the request.** `tasks/delegate.py` raises `RequestBroker.raise_blocked` after a round whose
outcome is `environment_stuck` (only when the runtime carries a broker and the team is not closed): kind `blocked`, `answers=[]`,
`expires_at=""`, `lands="next_round"`, `detail` the redacted last failing output. Nothing is held (no `hold`), so it never
expires and `_pending_json` sends `expires_in_s: null`. `FleetDaemon.steer` and `.approve` call `RequestBroker.supersede`
(new resolution `superseded`, `by="person"`) after the message is delivered; `end_team` still ends it `cancelled`/`abandoned`.
The card is a "Stuck" card with no buttons ("Fix the environment, then steer the role"), and a resolved blocked request is
labelled "Ended: ...", never "Denied".
Found by the MCP pass, fixed before merge: the steering grace (5 s) ended the team right after the round, so the card was
"abandoned" before anyone could steer; a stuck role in a steerable team now waits for a steer with no timeout (`team.py`). The
request-resolved log line also lacked the project id (the broker binds it), and `answer_request` now says blocked requests
take `steer_project`.

V5-E6a (ADR-0029): **more installs.** `envprep` plans poetry (`poetry install`, `POETRY_VIRTUALENVS_IN_PROJECT`), pipenv (`sync` or
`install`, `PIPENV_VENV_IN_PROJECT`), Go (`go mod download`), Rust (`cargo fetch [--locked]`), Ruby (`bundle install`, `BUNDLE_FROZEN`
with a lock) and Java (`mvn`/`./mvnw dependency:resolve`, `gradle`/`./gradlew dependencies`). `PrepareStep` gained `env` (settings for
its commands only); `_ALLOWED_PROGRAMS` grew and `./mvnw`/`./gradlew` resolve against the project root. Go, Rust, Java and Ruby have
no folder to look for (`_NO_FOLDER`), so they are due when `env.json` has no record, a changed fingerprint or a failed one.
`merge_env(tools=True)` passes their package settings (`POETRY_`, `PIPENV_`, `CARGO_`, `BUNDLE_`, `GEM_`, `MAVEN_`, `GRADLE_`, `GOPROXY`...)
to an install, never an agent. `environment.brief` says how to run things for each. Found by running the real `go`, `cargo` and
`poetry` on empty projects: an install can write its own lockfile (`cargo fetch`, `poetry install`, and `uv sync` with no `uv.lock`
already), which made the very next start see a "change" and install again. The daemon now records `fingerprint_now` (the files after
the install). Java was not run (no Maven or Gradle here).

V5-E6b (ADR-0029): **monorepo subfolders.** `detect` runs the same detectors on the root and then on each folder directly under it
(`_subfolders`: not hidden, not `node_modules`/`vendor`/`target`/`dist`/..., not a symlink), tags each hit with `EcosystemEnv.path`, skips
an ecosystem the root already has (workspaces), and stops at `MAX_SUBPROJECTS` (12). `PrepareStep.path` makes `run_step` run in that
folder; `env.json` keys are `node:frontend` for a nested project and the bare ecosystem for the root (so old files stay valid);
`EnvironmentPrepareStarted`/`EnvironmentPrepared` gained `path` (default `.`). The agent's `PATH` overlay is unchanged (root only):
`environment.brief` gives a nested project its own line (where it is, `cd` there, its `.venv` is not on `PATH`). The dashboard keys rows,
steps and progress by ecosystem and folder and says "Node in web/".


V5-ask (ADR-0028's update, after E5): **a kopicode `ask` question can be answered live.** kopicode v0.4.0
released `ask.request`; `ask_mode: "remote"` on `session.start` makes kopicode send it instead of its fixed
"no human is present" reply. `serve_supports_ask` reads `ask.request` from `kopicode version --json` (once per
binary), and `KopicodeBackend._ask_handler` wires it only when a person can be asked and the child has
`--consent-timeout`. `ServeChild._answer_ask` relays `{question, context}` to `ShellAsker.ask_person`, which
raises a `question` request (`RequestBroker.raise_question`, answers `answer` and `decline`) and holds it; an
answer replies `{text}`, and a decline, expiry, stop or child exit replies an error, which kopicode takes as
unanswered. `RequestResolved` gained `answered`, `declined` and `text` (redacted like the rest of the line);
the API and MCP `answer_request` take `text`. The card is a text box with Send answer and Decline. Recent
activity now says "Asked a question: ..." because the tool-call row cannot say whether anyone answered.
Sandboxed serve sessions get the handler too. Tests: `test_kopicode_ask.py` against the fake (which has an
`ask` step), plus the HTTP round trip.

V5-limits (ADR-0030, after V5-ask): **a team can keep going on its own.** `cuttlefish.limits` holds the three
settings (`CUTTLEFISH_MAX_TURNS` 100, `CUTTLEFISH_SESSION_TOKEN_BUDGET` 5M, `CUTTLEFISH_MAX_CONTINUATIONS` 20).
`serve_features` reads `version --json` once per binary; with `session.limits` listed, `KopicodeBackend` sends
the first two on `session.start`. In `run_team`, a round that fails with `max_turns` or `budget_exhausted`
(`limits.CHECKPOINT_STOPS`) and was not steered, approved or stuck journals `RoundContinued` and re-enters the
steering path with a "continued automatically" heading and the latest handover. `max_continuations` is also a
`TeamInput` field. Only `run_team`; `workflow.py` is unchanged. Tests: `test_team_continue.py` (one fake process
plays every round, because the pool keeps one child per team), `test_limits.py`.

V5-guards (ADR-0030's update): **the brakes for a team that runs for days.** `CUTTLEFISH_ROUND_TIMEOUT` is passed
as `timeout` to `run_kopicode_serve`; a timeout is now `_timeout_outcome` (`failure_kind="round_timeout"`, edits
kept) in `CHECKPOINT_STOPS`, not a `DelegationError`. In `run_team`, `idle_rounds[role]` counts checkpoint rounds
with no `edited_paths`; at `CUTTLEFISH_MAX_IDLE_ROUNDS` the role is not continued, `_raise_no_progress` raises a
`blocked` card, and the role waits for a steer (no timeout) that resets the count. Tests: `test_team_continue.py`,
`test_limits.py`, `test_kopicode_serve.py`.

V5-restore-cards: **a restart no longer loses a Stuck card.** `FleetDaemon.resume_pending` works out which teams it
will resume first, then `sweep_abandoned(keep_blocked=...)` skips their unresolved `blocked` requests (every other
kind is still abandoned: its agent process is gone), and `_launch_team` calls `RequestBroker.restore_blocked` with the
journal's unresolved requests. Nothing is journaled again; steering or the team ending resolves the same id.

V5-handover-content: **a handover says what the agent did.** `handover._texts` (token estimate and the summariser's
input) now includes `ToolCallRecorded`, a refused `ConsentDecided`, `DelegationFailed.detail` and
`DelegationCompleted.edited_paths`, and the prompt asks for files, done, remaining and failures; a continued round's
summary lists its changed files. Found by the first real-kopicode run of `test_context_refresh_live.py` (see ADR-0030's
update); it passed twice afterwards (2 of 4 files once, 4 of 4 across 4 sessions once).

V5-refused-continues (ADR-0030's second update): found by the first manual run on real kopicode. `team._checkpoint_reason`
treats a `refused` round as a checkpoint (`RoundContinued` reason `refused`, `_continue_text` names the refused commands)
and the idle guard bounds it. `SessionRecord.written_paths` (read in `run_kopicode_serve` for every local session) feeds
the whole-file writes whose path the stream's cut arguments lose into `classify_turn` as `edit_applied` events;
`classify_stream` dedupes edited paths. Tests: `test_team_continue.py`, `test_kopicode_serve.py`, `test_stuck.py`.

V5-small-items: **tidying what live runs and the first browser pass found.** The bottom nav bar and the project tab row
fit 320px and 390px (`Shell.svelte`, `Tabs.svelte`). `environment._node` counts `node_modules` as an install only when it
has something in it, so a hand-made empty folder no longer gets "dependencies are in node_modules" in the brief.
`PreparePlan.nothing_to_install` (from the `produced: false` record) reaches the card as `prepare.nothing_to_install`, which
reads "no dependencies to install" instead of "node_modules is missing". The activity log reads a round that ran out of room
and was continued as "Round ended" (`checkpointedRounds`, derived from the journal, nothing new stored), and cuts a long
"Round started" prompt with the whole text under "Whole prompt". `FleetDaemon._launch_team` no longer raises when a
stopped team's run has no terminal event, so a stop logs no ERROR traceback.

V5-context-pressure (ADR-0030's third update): `ServeChild.watch_context` and `_check_context` ask `session.usage` on each
`provider_response` (one in flight per session) and cancel the session once `context_tokens` passes
`CUTTLEFISH_CONTEXT_LIMIT_PERCENT` of a known `context_window`; `run_kopicode_serve(context_limit=...)` turns that into a
`context_pressure` outcome (`_pressure_outcome`), which is in `limits.CHECKPOINT_STOPS` so `run_team` continues it.
`KopicodeBackend._context_limit` passes a limit only when `version --json` lists `session.usage`, `usage.context` and
`usage.context_window`. The fake serve has a `{"usage": {...}}` step. Tests: `test_kopicode_serve.py`,
`test_team_continue.py`, `test_limits.py`.

V5-live-findings (ADR-0030's fourth update, from the first 100-turn real run): `maybe_handover(force=True)` is called at
every `RoundContinued` in `run_team` (a failure is logged, never fatal); `_with_reported_cost` sets
`DelegationOutcome.cost_usd` from a kopicode turn result's `usage.cost_usd`; `ServeChild` runs the work for a session
(consent, ask, stuck and context checks) in a copy of the logging context its `register` was called in. Tests:
`test_team_continue.py`, `test_kopicode_serve.py`.

V5-limit-settings (ADR-0030's fifth update): `limits.LIMIT_SPECS` and `validate_limits`/`merge_limits`/`*_for` resolve
role over project over environment. `projects.limits_json`, `RoleDefinition.limits`, `PersistedRole.limits`;
`_build_role_inputs` composes `RoleInput.limits`; `team._backend_kwargs` passes it to the delegate task and `run_team` reads
the per-role counters; `KopicodeBackend.delegate(limits=...)`. `GET /api/limits`, `PATCH /api/projects/{id}/limits`.
Frontend: `lib/limits.ts`, `LimitsEditor.svelte`, the Team tab. Tests: `test_limits.py`, `test_project_store.py`,
`test_daemon.py`, `test_team_continue.py`, `test_fleet_server.py`, `limits.test.ts`.

