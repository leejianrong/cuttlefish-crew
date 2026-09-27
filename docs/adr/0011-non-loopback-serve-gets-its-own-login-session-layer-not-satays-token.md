# ADR-0011: A non-loopback `cuttlefish serve` bind gets its own password/session layer, layered above satay's own guard, never a loosened version of it

- Status: Accepted
- Date: 2026-09-27
- Deciders: Jian

## Context

KAN-1706 (CUT-E9, slice E's first card): `cuttlefish serve` binds loopback only
and authenticates every request with one shared bearer token
(`x-cuttlefish-token`), generated fresh at process start and printed once to
stdout — reusing `satay.control.SecurityPolicy`/`ensure_loopback_bind`/
`generate_token` directly (ADR-0009's own reasoning: this daemon is a strictly
higher-value target than a single task's own control API, so it borrows the
identical posture rather than inventing a separate one).

That posture is satay-runtime's own ADR-0014, and ADR-0014 is explicit about
what it is *for*: "a per-session token... an `Origin`/`Host` allow-list... a
loopback-bind check" — proportionate to the actual threat a loopback-only,
browser-reachable local port faces (another tab on the same machine, or a
DNS-rebinding page reading `127.0.0.1` back). `satay.control.ensure_loopback_bind`
enforces this by refusing to bind anything else outright:
`NonLoopbackBindError` names the reason directly in its own message —
*"the local-surface guard is not network authentication"* — a design boundary
stated in the code, not an oversight to work around.

KAN-1706 asks for exactly the thing that boundary says the loopback guard was
never built for: reaching the dashboard from somewhere that isn't the operator's
own machine — a Paperclip-style `authenticated+private`/`authenticated+public`
mode, per `docs/QUESTIONS.md`'s own framing of the gap
(`project_paperclip_positioning`, "cuttlefish-crew has none of this — loopback-
only, no auth mode").

Two options exist once a non-loopback bind is actually wanted:

1. Stop calling `ensure_loopback_bind` and keep using the identical shared,
   printed-once static token as the only guard, regardless of bind address.
2. Build a second, distinct guard for the non-loopback case specifically, and
   keep the existing loopback+static-token path completely unchanged for the
   (still default, still recommended-for-local-use) loopback case.

Option 1 is the one this ADR rejects, and rejects on the plainest possible
evidence: it is the literal thing satay's own `NonLoopbackBindError` message
warns against. A static token good for the life of the process, with no
expiry, is proportionate when the only way to see it is already being on the
operator's own machine (reading the daemon's own stdout) — it stops
becoming proportionate the moment the bind is reachable from a LAN, a
Tailscale tailnet, or the open internet, where the token can leak into a
shared shell history, a screen-share, a proxy log, or simply be guessed at
leisure with no lockout and no way to invalidate one copy without restarting
the whole daemon.

## Decision

**A non-loopback bind requires `CUTTLEFISH_SERVE_PASSWORD` (env var only, never
a CLI flag — `handling-secrets` discipline: a flag lands in shell history and
`ps`, an env var set by the caller's own shell does not) and switches the
daemon onto a completely separate security layer, `cuttlefish.fleet.auth
.SessionAuth`, instead of `satay.control.SecurityPolicy`.** The loopback,
default path is untouched — same static token, same `ensure_loopback_bind`
call, same one-line startup message, so every existing test and the
`make demo` local flow keep working exactly as before.

`SessionAuth` is a single-operator login: one password, checked with
`secrets.compare_digest`; a `POST /api/login` mints a short-lived (12h
default), HMAC-signed session token (`{expiry}.{hex-mac}`, signed with a
random-at-startup, never-persisted 32-byte secret) instead of handing back the
password itself or a second static secret. A session that leaks expires; the
daemon restarting invalidates every session at once (the same "restart to
revoke" posture the existing static token already has, not a regression).
Five consecutive wrong passwords lock further attempts out for 30 seconds — a
cheap, real guard against a naive brute force that a static, never-expiring
token had no equivalent for. `CUTTLEFISH_SERVE_PASSWORD` shorter than 12
characters is refused at daemon startup (`WeakPasswordError`), not silently
accepted — a password guessable in an afternoon defeats the entire point of
this ADR.

`cuttlefish.fleet.server.create_app` accepts either guard through one
structural protocol (`SecurityCheck`, `check(token=, host=, origin=)`) so the
FastAPI app and its one security middleware do not know or care which mode is
active. Two routes are exempt from the check entirely and exist in both modes:
`GET /api/auth-mode` (so the dashboard can tell, before it has any credential
at all, whether to render a token field or a password field) and
`POST /api/login` (a 404 in loopback/static-token mode, where no login step
exists).

**Origin allow-listing becomes an explicit, operator-supplied list
(`--allow-origin`, repeatable) in non-loopback mode**, added to
`CORSMiddleware`'s existing loopback-only regex rather than replacing it —
the loopback regex still covers same-machine dev use even when bound
elsewhere; the new list covers wherever the operator will actually browse
the dashboard from (a Tailscale MagicDNS name, a real domain once KAN-1710/
1719 exist). An empty list in non-loopback mode means only same-machine
browser access works out of the box — the honest default, not a silent
wildcard. `SessionAuth.check`'s own `Origin` check carries the identical
loopback carve-out `satay.control.SecurityPolicy.check` already has, found
live and not just reasoned about: without it, the dashboard's own Vite dev
server (`http://localhost:5183`) talking to a non-loopback-bound daemon on
`127.0.0.1` was rejected outright, since its origin matched neither the
(empty-by-default) `allowed_origins` set nor anything else — same-machine
code is the one thing already trusted in either auth mode, so it gets the
same carve-out here that it already has in loopback mode.

**`Host`-header DNS-rebinding defence is deliberately not carried over to the
non-loopback case** (unlike `Origin`, above). `is_loopback_host` on `Host`
would only mean something if every legitimate `Host` the daemon is reached by
were loopback, which is false by construction once the bind is intentionally
non-loopback; the meaningful anti-rebinding control left is the explicit
`--allow-origin` list plus the login/session token itself, not a `Host`
check that would have to allow-list every address the operator might
legitimately reach the daemon by. This is a real, named scope reduction
relative to ADR-0014's loopback guard, not silently dropped.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Keep the identical static, printed-once token and only relax `ensure_loopback_bind` | Rejected above — this is the specific thing `NonLoopbackBindError`'s own message says the guard was never meant to survive being turned into. |
| Extend `satay.control.SecurityPolicy`/`ensure_loopback_bind` itself to support a password-login mode | Violates the standing "no changes to satay-runtime beyond a concrete, narrowly scoped ask" boundary (`docs/SLICES.md`'s Out list, Q33) for no real gain — this daemon's own login concern (one operator, one password, one dashboard) has nothing to do with satay's per-task control API, which stays loopback-only, single-token, exactly as ADR-0014 designed it. |
| Full multi-user accounts (usernames, roles, an actual user table) | Out of scope per `docs/PLAN.md`'s own line: "Any actual multi-tenancy, auth, or isolation-between-operators implementation" is explicitly not being built yet — KAN-1706 asks for real auth for *one* operator reaching *their own* daemon remotely, not a multi-tenant product. Revisit only if a second, distinct operator identity is ever a real requirement. |
| A third-party session/JWT library | No new dependency earns its keep for "sign an expiry with HMAC and check it" — the same proportionate-dependency discipline `docs/QUESTIONS.md` Q51 already applied to the pixel-art renderer. |

## Consequences

Two security postures now coexist by design, not by accident: loopback stays
exactly as fast and frictionless as `make demo` needs it (no password to set,
same static token, same one stdout line), while a non-loopback bind is
strictly harder to reach for good reason and refuses to start at all without
an operator-supplied password. `docs/QUESTIONS.md`/`CLAUDE.md`'s prior "no
auth mode" gap is closed for the single-operator case KAN-1706 actually asks
for; the still-open items are `docs/PLAN.md`'s own named non-goal (real
multi-tenancy/multi-operator isolation) and KAN-1708 (Tailscale/tunnel mode,
which composes with this ADR rather than replacing it — a tailnet address is
still a non-loopback bind needing this same login layer, just reached over a
narrower network).
