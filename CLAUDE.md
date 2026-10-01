# CLAUDE.md — agent brief for cuttlefish-crew

cuttlefish-crew is a fleet manager for small teams of coding sub-agents across
many software projects, observable from a dashboard. It is built on
[satay-runtime](https://github.com/leejianrong/satay-runtime) for durable
workflow execution and delegates the actual coding through a pluggable
`AgentBackend` seam: [kopicode](https://github.com/leejianrong/kopicode) (the
reference), headless Claude Code, headless Codex. Python, `uv`, `ruff`,
`mypy --strict`, `pytest`; the dashboard is Svelte + TypeScript in `frontend/`.

## Secrets

- **Never read or open `.env`.** It holds the real `OPENROUTER_API_KEY` for
  this repo. Refer to `.env.example` instead — the committed template with
  no real values.
- **Never read or open `.cuttlefish/secrets.db`.** It's encrypted at rest,
  but still holds every project's real secrets; use `cuttlefish secrets
  get/list` instead. Never log or print `CUTTLEFISH_SECRETS_KEY` itself —
  losing it is equivalent to losing every secret it protects.

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

## Gotchas that have already cost a session

- **Only the fleet daemon resumes after a crash.** `cuttlefish run`/`run-team`
  mint a fresh id (`cli.py`) and ADR-0010's `--resume` flag was never built.
  Never write "a killed process resumes" without saying which one.
- **One backend per process.** `CUTTLEFISH_AGENT_BACKEND` applies to every
  project and role a given `cuttlefish serve` runs.
- **`OPENROUTER_API_KEY` is only needed once a handover summary is due**
  (the provider is built lazily, KAN-1807); without it that call fails, unless
  `CUTTLEFISH_LLM_PROVIDER=replay` (placeholder summaries). It is cuttlefish's
  own summarising provider, not the coding agent's credential.
- **`cuttlefish run` writes `.cuttlefish/` and `.satay/` into the current
  directory**, and the daemon writes them into each project's `--root`.
- **The code beats prose.** Check a claim against the code before repeating
  it, and update the README and `docs-site/` in the same PR as any behaviour
  they describe (`make docs` builds the site with `--strict`).

## How to work here

- `main` is PR-only. Branch per slice part: `git switch -c feat/<slice>-<part>`
  off `origin/main`, then open a PR. `make ci` green before merging.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- `make check` (lint + `mypy --strict`), `make test` (fast, unit-only),
  `make ci` (the full suite `make test-all`, plus `frontend-check`/
  `frontend-test`/`frontend-build`, gates on). CI additionally builds
  kopicode from source so the delegation's integration tests run against
  the real binary, not a mock.
- `make demo` (`scripts/demo.sh`) builds the dashboard and runs one
  `cuttlefish serve` process that serves both the API and the UI, printing
  the URL/token to paste in. Hot-reloading the frontend means running
  `frontend`'s own `npm run dev` beside `cuttlefish serve`. Bare `make`
  always shows `make help`, never runs a target by accident — keep any new
  target's `##` comment and this ordering intact.

## Where the deeper docs live

`docs/` describes the intended system; where it disagrees with the code, the
code is the truth (`ls src/cuttlefish/`, `git log --oneline`, and a module's
own doc comment all beat a paragraph). Start reading at
`src/cuttlefish/workflow.py` (single-task loop), `team.py` (multi-role loop),
`agents/` (backend seam), `fleet/` (daemon and HTTP surface).

- [`agent_docs/known-gaps.md`](agent_docs/known-gaps.md) — limitations that
  were accepted on purpose; read before proposing to "fix" one.
- [`agent_docs/what-is-built.md`](agent_docs/what-is-built.md) — the history
  and rationale of each shipped slice.
- [`docs/PLAN.md`](docs/PLAN.md) — the current problem, scope and shape.
- [`docs/adr/`](docs/adr/) — why each load-bearing decision was made.
- [`docs/QUESTIONS.md`](docs/QUESTIONS.md) — every decision, who made it, and
  where it landed; [`docs/SLICES.md`](docs/SLICES.md) — the build order.
- [`docs/research/`](docs/research/) — the hands-on Paperclip comparison.
- Pandan board `cuttlefish-agent` (key `CUT`) — build-plan progress.
