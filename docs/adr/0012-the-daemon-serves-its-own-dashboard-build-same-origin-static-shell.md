# ADR-0012: `cuttlefish serve` optionally serves the dashboard's own production build, same-origin, unauthenticated as a static shell

- Status: Accepted
- Date: 2026-09-27
- Deciders: Jian

## Context

KAN-1707 (CUT-E9, second card): `cuttlefish serve` today exposes only the JSON
API (ADR-0009); actually seeing the dashboard means also running
`frontend`'s own Vite dev server (`make demo`/`scripts/demo.sh`) — two
babysat processes even to look at one project on one laptop, and a dev-only
story (`npm run dev`) with no equivalent for anywhere `cuttlefish serve` might
run without a full frontend toolchain installed (a remote host, ADR-0011's
own non-loopback case, the eventual KAN-1710 hosted spike).

Three real design questions, not just "add a route":

1. **Where does the daemon look for a built dashboard?** `frontend/dist`
   already exists (`make frontend-build`, gated in CI). Bundling it into the
   installable Python artifact itself (`package_data`, a wheel that carries
   its own static assets) is real future packaging work, tied to however
   `cuttlefish` actually gets *distributed* once KAN-1710's hosted-deployment
   spike lands — out of scope here, where `cuttlefish serve` still only ever
   runs from a checked-out repo (`uv run cuttlefish serve`). A directory path,
   resolved relative to wherever it's actually invoked from, is the honest
   scope for this card.
2. **Does the static shell need its own auth?** No — the built dashboard is a
   generic client (no secrets baked in; it prompts for whatever base URL and
   credential the operator gives it, ADR-0011's own `ConnectScreen`). Gating
   the page load itself behind a token/password would be friction with zero
   security benefit, since every API call it then makes is still checked by
   `_check_security` exactly as before. The one thing that changes is where
   the *boundary* sits: at every `/api/...` route, not at the origin.
3. **Does this need a client-side router / SPA-fallback route?** No —
   `App.svelte` is plain `$state`-driven view switching (`ConnectScreen` /
   `Portfolio` / `ProjectDetail` / `SpriteGallery`), no URL-based routing at
   all today. A browser only ever requests `/` (and `index.html`'s own
   `assets/*.js`/`*.css`) — there is no second URL a refresh could land on
   that plain static serving wouldn't already handle correctly.

## Decision

**`create_app`/`run_daemon` gain an optional `dashboard_dir`.** When given, a
`fastapi.staticfiles.StaticFiles(directory=dashboard_dir, html=True)` mount is
registered at `"/"`, *last*, after every `/api/...` route — Starlette matches
routes in registration order, so the API is untouched and the mount only ever
catches whatever none of those routes matched.

**The security middleware only ever checks `/api/...` paths.** `_check_security`
changes from an explicit public-path exemption list to `path.startswith("/api/")
and path not in _PUBLIC_PATHS` — the dashboard's static files (and, if no
`dashboard_dir` is configured, a plain 404 at `/`) never go through
`security.check` at all, in either ADR-0011 auth mode.

**The CLI resolves the path, `run_daemon` just trusts it.** `--dashboard-dir
PATH` is explicit and strict: given, and missing `index.html`, `run_daemon`
raises before binding anything — an operator who typed the flag asked for this
and gets told clearly if it's wrong, not a silent fallback to API-only. With no
flag, `cuttlefish._resolve_dashboard_dir` probes a single best-effort default,
`./frontend/dist` relative to the current directory (matching how
`scripts/demo.sh` already `cd`s to the repo root before invoking it) — missing
there is not an error, since nothing was ever explicitly asked for; the daemon
just serves the API alone exactly as it always has, the same default any
existing script or test invoking `cuttlefish serve` already expects.

**`make demo`/`scripts/demo.sh` become one process, not two.** The script now
builds the frontend once (if `frontend/dist` is missing or `frontend/src` is
newer, a plain mtime check, not a content hash — cheap and correct enough for
a local dev script) and runs `cuttlefish serve` alone; the daemon auto-detects
and serves that build, and prints one URL to open, not a URL-plus-token-to-
paste-into-a-second-process. Developing the frontend itself (hot reload) still
means running `cd frontend && npm run dev` and `cuttlefish serve` in two
terminals as before — that workflow is unchanged, just no longer what `make
demo` itself demonstrates.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Bundle `frontend/dist` into the Python package itself (`package_data`/`MANIFEST.in`) so an installed `cuttlefish` (not just a repo checkout) can serve it | Real work, but tied to a packaging/distribution story (`pip install cuttlefish-crew` from a built wheel, or a container image) that doesn't exist yet — `docs/SLICES.md`'s own "Slices E, F: not yet fully planned" note already places distribution alongside KAN-1710's hosted-deployment spike, not this card. A directory-path default that matches today's one and only real invocation shape (a repo checkout) is the honest scope. |
| A full SPA-fallback route (serve `index.html` for any unmatched path, not just `/`) | Solves a problem the dashboard doesn't have — `App.svelte` has no client-side router, so there is no second URL a browser could ever legitimately request. Adding fallback routing now would be building for a router that doesn't exist, the same discipline `docs/QUESTIONS.md` Q51 already applied to the pixel-art renderer (no dependency/mechanism until a real need shows up). |
| Gate the static dashboard behind the same token/session check as the API | No security gained (the shell has no secret; every API call it makes is still checked) for a real cost (an operator can no longer just open the URL to see *that a daemon is even running* before they have a credential in hand — a legitimate first thing to check, especially for the non-loopback case ADR-0011 added). |

## Consequences

`make demo` genuinely becomes the "one deployable artifact" KAN-1707 names —
one process, one URL, no token-paste step for the common case (loopback,
static-token mode still requires pasting the printed token into
`ConnectScreen`'s credential step; that part is unchanged and orthogonal to
this ADR). Combined with ADR-0011's non-loopback mode, an operator can now
run `cuttlefish serve --host <tailnet-addr> --dashboard-dir frontend/dist`
and reach a real, authenticated dashboard from another machine with nothing
else running. Real packaging (an installable artifact carrying its own
static assets, not a directory-path convention tied to a checkout) remains
future work, named honestly here rather than solved.
