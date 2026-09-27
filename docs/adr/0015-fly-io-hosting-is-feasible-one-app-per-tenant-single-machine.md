# ADR-0015: Fly.io hosting is feasible as one app per tenant, one machine, HOME-relocated onto a persistent volume — not deployed by this spike

- Status: Accepted (spike findings; no live deployment)
- Date: 2026-09-27
- Deciders: Jian

## Context

KAN-1710 (CUT-E9, fifth and last card): "package the fleet daemon + built
dashboard as one Fly app. Establish what a hosted trial actually requires:
per-tenant isolation story, persistent volume for episodic/project stores,
secrets handling on a shared host." Explicitly a spike — its own description
says it "feeds directly into F4's hosted-service card," not that it ships a
hosted product itself.

**Scope, agreed with Jian before starting**: design + a real, locally-built-
and-run `Dockerfile`/`fly.toml.example`, but no live `fly launch`/`fly deploy`
against his real, already-authenticated Fly.io account — provisioning real
billable infrastructure under his name is exactly the kind of action that
needs his own sign-off, not an autonomous default, so this was checked with
him directly rather than assumed. Everything below was verified by actually
building and running the image locally (`docker build`/`docker run`, this
sandbox's own real Docker daemon) — never against Fly's actual service.

## What was actually built and verified

A three-stage `Dockerfile` (frontend build → kopicode-from-source build,
mirroring `.github/workflows/ci.yml`'s own approach exactly, since no
published `kopicode` binary exists to just install → the Python runtime,
`uv sync --frozen --no-dev`) builds successfully and produces a **260MB**
image. Run locally with `docker run -e CUTTLEFISH_SERVE_PASSWORD=... -p
8560:8420`: `/api/auth-mode` reports `"password"` correctly (ADR-0011),
`POST /api/login` mints a real session token, a project registers
successfully through it, the dashboard's own `index.html` serves from the
same origin (ADR-0012), and `kopicode --version` runs from `/usr/local/bin`
inside the container — the whole stack, verified live, not just written.

**Two real bugs found and fixed during this build**, not hypothetical:

1. `uv sync --frozen --no-dev` failed outright with `OSError: Readme file
   does not exist: README.md` — `pyproject.toml`'s own `readme = "README.md"`
   makes `hatchling` (the build backend) refuse to build at all without it.
   Fixed by also `COPY`-ing `README.md` before `uv sync`.
2. The original `CMD` ran a plain `uv run cuttlefish serve`, which re-syncs
   against the environment on every invocation — and re-syncing pulls in the
   **full** dependency set, including the `dev` group `--no-dev` deliberately
   excluded at build time. Every single container start (and every
   scale-to-zero wake, see below) was re-downloading `mypy`/`ruff`/etc. from
   the network before ever running `cuttlefish serve` — real, measurable
   cold-start latency and a runtime network dependency the image should
   never have needed. Fixed with `uv run --no-sync`, which trusts the venv
   already built into the image rather than re-resolving it.

## Findings that answer the card's own three questions

**Per-tenant isolation**: this architecture has no shared-tenant story to
build, because it never had a single-tenant one to begin with in the
opposite sense — `ProjectStore`/`SecretsStore`/each project's own
`.cuttlefish`/`.satay` stores are all SQLite, single-writer-per-store by
construction (ADR-0009/ADR-0012's own standing discipline). One Fly *app*
per tenant — one operator's own password, one project registry, one set of
secrets — is the only shape that fits without inventing new isolation
machinery this spike was never asked to build. This is the concrete answer
F4's own hosted-service pricing/wrapper card needs: "hosted" means
*provisioned per tenant*, not a shared multi-tenant instance, consistent
with `docs/PLAN.md`'s own standing "no multi-tenancy built yet" position
(ADR-0002's 2026-09-20 addendum) — this spike doesn't change that position,
it just states its practical consequence for Fly hosting specifically.

**Persistent volume**: a single Fly volume, mounted at `/data`, with `HOME=
/data` set in the app's own env — `ProjectStore`/`SecretsStore` already
resolve relative to `HOME` by default, so this relocates the top-level
registry/secrets store onto the volume with *no code change at all*. The
one real gotcha (found by actually reasoning through the full path, not
assumed): a registered project's own root (`--root`) must **also** live
under `/data` (e.g. `/data/projects/<name>`) for *its* `.cuttlefish`/`.satay`
stores to survive a restart — `HOME` alone only relocates the registry, not
each project's own working tree. Documented directly in `fly.toml.example`'s
own comments, since it's exactly the kind of thing an operator would
otherwise discover the hard way after their first restart wipes a project's
history. A single Fly volume is attached to exactly one machine, so this app
must stay a single machine — not a new constraint Fly's own model imposes,
just one satay's single-writer discipline already required.

**Secrets on a shared host**: this composes cleanly, no new work needed.
`fly secrets set CUTTLEFISH_SERVE_PASSWORD=...` (Fly's own encrypted-at-rest
secret store) injects exactly the env var ADR-0011 already reads — the same
mechanism covers `CUTTLEFISH_SECRETS_KEY` and any backend credential
(`ANTHROPIC_API_KEY` etc.) identically.

## Two findings beyond the card's own three questions, worth recording

**TLS is free here, unlike the Tailscale-direct-bind case.** ADR-0013 named
"no TLS" as an accepted gap for `--tailscale` specifically (WireGuard already
encrypts the transport, but no browser padlock). Fly's own edge (`fly-proxy`)
terminates real TLS at `<app>.fly.dev` and forwards plain HTTP internally —
this gap is simply solved for the hosted case, a genuine cross-ADR synergy,
not something this spike had to build.

**`--allow-origin` is mandatory here, and ADR-0013's self-origin-trust
default does not cover it.** `_self_origin` (ADR-0013) trusts an `Origin`
that matches the daemon's own *internal* bind (`http://0.0.0.0:8420`) — but a
browser reaching the app through Fly's edge sends `Origin:
https://<app>.fly.dev`, a completely different string. Without an explicit
`--allow-origin https://<app>.fly.dev`, the dashboard would 403 itself the
moment it tried to call its own API through Fly's edge. Found by actually
tracing the request path end to end, not assumed — documented directly in
`fly.toml.example`'s own comments as a required step, not an optional one.

**Scale-to-zero composes correctly with this project's own crash-safety
story.** Fly's `auto_stop_machines`/`auto_start_machines` (stop the machine
when idle, restart it on the next request) is, from cuttlefish's own
perspective, indistinguishable from the supervised restart ADR-0014 already
proved works — `FleetDaemon.resume_pending` runs on every `cuttlefish serve`
startup regardless of *why* the previous instance stopped. This means a
genuinely cheap "pay only while in use" hosted tier is architecturally sound
here, not a future risk — a real, positive finding for F4's own pricing work.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Actually deploy a live Fly app this session | Explicitly declined by Jian when asked directly — provisioning real infrastructure under his account is a decision for him to make, not to default into. |
| A shared multi-tenant instance (one Fly app, many operators) | Would need real per-tenant isolation (separate project registries, separate secrets, separate auth) this architecture doesn't have and this spike wasn't asked to build — ADR-0002's own standing position. One-app-per-tenant is the honest shape *today's* architecture actually supports. |
| Multiple Fly machines for horizontal scaling | Breaks on first principles — a Fly volume attaches to one machine, and every store here is single-writer SQLite. Scaling this design means running more *tenants* (more apps), not more machines behind one app. |

## Consequences

The technical shape of hosted deployment is now known and locally proven,
not theoretical: a working `Dockerfile` (two real bugs found and fixed by
actually building it) and a schema-valid `fly.toml.example` (`flyctl config
validate --strict` passes, checked without touching Fly's live API) exist in
the repo for whenever a real deploy is greenlit. F4's own hosted-service card
now has a concrete answer to build a pricing/product wrapper around: one app
per tenant, scale-to-zero-compatible, TLS included for free, `--allow-origin`
required, a persistent volume with two things that must live under it
(`HOME` and every registered project's own `--root`). Nothing here has been
verified against Fly's actual production edge, DNS, or billing — only
locally, with real Docker, against everything that doesn't require Fly
itself.
