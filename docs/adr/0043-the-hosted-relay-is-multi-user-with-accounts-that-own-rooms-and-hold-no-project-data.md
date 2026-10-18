# ADR-0043: The hosted relay is multi-user, with accounts that own rooms and hold no project data

- Status: Accepted (Jian, 2026-10-18)
- Date: 2026-10-18
- Deciders: Jian (multi-user from the start, 2026-10-18); the choices below are mine and are open to change

## Context

ADR-0037 built a relay with no accounts: a room is a name that a daemon claims by connecting first, and a second daemon
under the same name replaces the first. That is fine on loopback and on a tailnet. ADR-0041 made the hosted relay the route
for everyone else, and on 2026-10-18 Jian decided that the hosted service is **multi-user from the first deploy**, because
the same service later becomes public: sign up, sign in with GitHub or Google, onboarding, then 20 to 100 agents on the
user's own PC watched from a phone. It need not be polished, but the schema and the checks must be the real ones, so
nothing is retrofitted when the doors open.

ADR-0037 also says accounts and hosting do not ship publicly until an outside reviewer has looked at pairing, tiers and
revocation. This ADR does not change that. It decides what the service is, so the relay (CUT-135) is written against it.

## Decision

### What the service is for, and what it never holds

The service answers three questions: who is this account, which rooms does it own, and is a room's daemon online. It
carries encrypted frames between a browser and a daemon and cannot read them (ADR-0037). **It stores no project data, no
model keys and no agent credentials, and a feature that needs one belongs on the PC instead.** Pairing, tiers and
revocation stay on the daemon (ADR-0039). An account grants no access to any PC: each browser still pairs with each PC.

### Accounts

- An **account** has an opaque id, a created time, a status (`active`, `suspended`, `deleted`), a contact email and a display
  name. Nothing else about the person.
- An account has one or more **identities**: `(provider, subject)`, unique across the service. Providers are `owner`
  (below), `github` and `google`.
- **Sign-in is the OAuth authorisation-code flow with PKCE and a `state` value**, with the narrowest scopes (`read:user
  user:email` for GitHub; `openid email profile` for Google). The provider's access token is used once to read the identity
  and then dropped. We never ask for repository access, and nothing from a provider is kept except subject, email and name.
- **An identity is never linked to an account by matching email.** A second provider is added from a signed-in account's
  settings. This costs a person a second account if they forget which they used, and it closes the takeover where someone
  registers an unverified email at another provider.
- A **session** is a random 256-bit value in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie. Only its hash is stored. It
  slides while used and has a hard limit of 30 days. Requests that change state check `Origin` against the service's own.
- **The single-user phase.** Until GitHub and Google exist (CUT-145), one `owner` identity signs in with a long random
  secret that the operator sets on the server (stored hashed). It is a real account row with real room ownership, so adding
  providers changes how a person proves who they are and nothing else.
- **Sign-up is closed by default** (`signups` off). An account is created only for an owner, an invited email, or after the
  operator turns sign-up on. It stays off until the outside review is done.

### Rooms: who may claim and who may join

- A **room** has an id (128 random bits, URL-safe), an owning account, a label the owner chose, a created time, a status and
  a **registration token**. It is created by a signed-in account, never by a daemon connecting first.
- The registration token is 256 random bits, shown once and stored as a hash. The daemon sends it when it dials in
  (`Authorization: Bearer`, over `wss`). The relay refuses a daemon whose token does not match the room, with no
  detail about whether the room exists. The owner can rotate it, which cuts the daemon's connection at once.
- **A valid token replaces its own stale connection** (a daemon that crashed and came back must be able to return), and
  **nothing else replaces anything**. A second daemon with a wrong or missing token is turned away; the first stays. This
  ends the "second daemon replaces the first" behaviour of ADR-0037.
- **Joining needs no account.** A phone that scans a pairing link has the room id and the daemon's key in the link, and the
  daemon's own pairing and confirmation are the gate (ADR-0039). Requiring a sign-in on the phone would add a second
  gate that protects no data, and make the pairing link depend on a cookie. The relay says only "the daemon is offline"
  for an unknown room, an offline one and a suspended one, so room ids cannot be probed. A browser connection is still
  counted against limits (below).
- A signed-in account sees **its own rooms and whether each daemon is online**, with byte counts, and nobody else's.

### Quotas and abuse limits

Numbers are defaults, set by configuration, and meant to be changed once there is data.

| Limit | Default | Why |
|---|---|---|
| Rooms per account | 5 | One per PC, with room to spare |
| Browsers per room | 16 | Already in the relay |
| Concurrent connections per address | 40 | A phone and a few tabs, not a flood |
| New connections per address per minute | 60 | Stops handshake floods |
| Sign-in attempts per address per minute | 10 | Stops guessing |
| Accounts created per address per day | 5 | Sign-up abuse |
| Relayed bytes per account per day | 5 GiB | A terminal and artifacts, not a file host |
| Rooms on the service | 1000 | Already in the relay; a ceiling while the process is one |

- The address is the client address from the proxy's `X-Forwarded-For`, trusted **only** from the proxy we run in front. The
  counters are in memory and are not written down.
- Going over a limit closes the connection with a close code and a sentence the dashboard can show. It never drops data
  silently.
- **An abuse switch per account:** `suspended` closes that account's live connections, refuses its rooms, and signs it out.
  The operator sets it with a command on the server (`cuttlefish relay-admin suspend <account>`, CUT-147), not through a web page,
  so there is no admin surface to attack. A reversal is the same command.
- One process serves the relay and holds rooms in memory, so this is a single-instance design. Moving to several instances
  would need shared room state and is not decided here.

### What it stores

A SQLite file on the Droplet's disk (single instance; a move to Postgres is a migration, not a redesign):

| Table | Holds |
|---|---|
| `accounts` | id, created, status, email, display name |
| `identities` | account id, provider, subject, created, a hash of the sign-in secret (the `owner` provider only) |
| `sessions` | session hash, account id, created, last used, expires |
| `rooms` | id, account id, label, created, status, token hash, last online |
| `usage_daily` | room id, day, bytes relayed, connections |
| `invites` | email, created, used |

**Never stored:** frames or anything inside them, paths, commands, output, artifacts, secrets, model keys, agent
credentials, a provider's access token, an IP address in the database. Caddy's access log (address, path, status, size) is
kept for 14 days for abuse handling and then rotated away; it holds no frame content because there is none in a URL.

### Deleting an account

A signed-in account can delete itself. In one transaction: sessions removed, rooms closed and their tokens deleted,
identities and the account row deleted, usage rows deleted. Live connections are cut first. Nothing about the person is kept,
except that **a daily database backup lives for 7 days**, so the data is gone from the service within 7 days of a deletion.
A daemon keeps working locally and over Tailscale; its paired phones lose only the hosted path. The terms and the privacy
notice that say all this in a person's words are CUT-148 and come before public sign-up.

### What the page is, and the limit that stays

The relay serves the dashboard build same-origin over https (CUT-135), which is what gives pairing its secure origin. The
limit in ADR-0037 stays and is said in the product: a compromised or hostile service could serve changed code. Accounts do
not remove it, and an account breach exposes which PCs exist and when they were online, not what they do.

### Where the code lives

Accounts, sessions, rooms and limits live inside `cuttlefish.relay.server` and import only the standard library, the web
libraries and `cuttlefish.relay.protocol`. A test already enforces that the relay imports nothing else from cuttlefish. The
daemon's connector gains one setting, the registration token, and no account logic.

## Order of work

| Card | Builds |
|---|---|
| CUT-135 | Rooms bound to an owner, one `owner` account, registration token, per-address limits, the page served by the relay, a Dockerfile and a Caddyfile |
| CUT-145 | GitHub and Google sign-in, a room per PC from the dashboard |
| CUT-146 | Onboarding: first PC, first phone |
| CUT-147 | Per-account quotas, the abuse switch and the daily byte cap |
| CUT-148 | Terms, privacy notice, account deletion |
| CUT-149 | The outside review, before sign-up opens |

CUT-135 creates the tables above in full, so the later cards fill in behaviour and do not alter the schema.

## Built so far

- **CUT-135** (2026-10-18): the schema above in full (`relay/accounts.py`), the `owner` account and its sign-in, sessions,
  rooms with registration tokens, per-address limits (`relay/limits.py`), the relay serving `frontend/dist` from its own
  origin, and `deploy/relay/` (a Dockerfile and a Caddyfile). Choices made while building:
  - The relay's own routes live under **`/relay-api/`** (sign-in, rooms), so they never collide with the daemon's `/api/`
    that the dashboard sends through the tunnel. A room is made by a signed-in account (`POST /relay-api/rooms`) or on the
    server with `cuttlefish relay-admin create-room`; either way the token is shown once.
  - **Two modes, one code.** `cuttlefish relay --db FILE` is the hosted service. Without `--db` the relay is open (no accounts,
    the old behaviour), and **it refuses any address that is not loopback**. The hosted image always passes `--db`.
  - The daemon sends its token as `Authorization: Bearer` and is refused with websocket close **4401** and no reason that
    says whether the room exists. Ten wrong tokens in a minute from one address lock that address out of the daemon route.
  - The session cookie is `__Host-cc_session` (`Secure`, `HttpOnly`, `SameSite=Lax`), sliding to 7 days idle and 30 days hard.
    State-changing routes also check `Origin`.
  - The relay reads `X-Forwarded-For` only with `--trust-forwarded`, and uses the **last** entry (the one the proxy added).
  - **A shared password in front of the whole site until the outside review** (Jian, 2026-10-18): the Caddyfile asks for it on
    every route except `/ws/daemon/*`, which is gated by the room's token instead. Remove it only when sign-up is meant to open.
  - The page is served with a Content-Security-Policy that allows scripts from the origin and the one inline theme script by
    hash, and connections to the origin only. Checked in Chromium and Firefox with no violations.
- **CUT-144** (2026-10-18): `make test-smoke` builds `deploy/relay/Dockerfile` and runs the image as deployed (`tests/smoke/`):
  the page and its assets load with the policy, the image runs as a non-root user, refuses an open relay on a public address
  and refuses to start hosted with no owner secret, a daemon with the token registers and one without is refused, a room
  survives a container restart, a real daemon (the fleet's own app and connector) registers and a browser pairs through it
  and reads `/api/projects`, and the repository's Caddyfile asks for the password on every route but the daemon's socket,
  including with `..`, encoded and doubled-slash paths. CI runs it on every PR (job `relay-image`); the deploy pipeline
  (CUT-141) runs it on the image it just built, by digest. The pairing step uses the Python protocol browser, not a real
  browser (Chromium and Firefox were checked by hand for CUT-135).
- **CUT-141** (2026-10-18): the pipeline (`.github/workflows/deploy.yml`, `.github/actions/deploy-relay`,
  `deploy/relay/relay-deploy.sh`; guide in `docs-site/hosted-relay.md`). After a green CI run on `main` it builds the image,
  pushes it to GHCR, records the digest, and smoke-tests that digest. A deploy job in the protected `production` environment
  (reviewers at first) runs the Droplet's one allowed command over a key restricted to it, with the image **by digest**;
  `relay-deploy` replaces the container, health-checks it and restores the previous one if it is unhealthy. Choices made:
  - The deploy key and the host key are environment secrets; the host key is pinned, never trusted on first use.
  - The deploy script is owned by root and not writable by the deploy user, so the key cannot rewrite what it may run. Updating
    the script means re-running the bootstrap (CUT-143), which is the one thing not done through the pipeline.
  - Nothing deploys until the repository variable `DEPLOY_ENABLED` is `true`, so the pipeline can land before the Droplet exists.
  - The GHCR package is public (it holds open-source code and the dashboard build, no secret; anonymous pulls were checked), so
    the Droplet pulls with no credential.
  - A rollback to an earlier release is the same workflow run by hand with a digest, through the same approval.
  - The deploy briefly stops the relay (seconds); daemons reconnect with their own backoff (at most 10 seconds) and browsers
    pair again on their next connection.
- **CUT-143** (2026-10-18): `deploy/droplet/bootstrap.sh` and `verify.sh` (guide in `docs-site/hosted-relay.md`). Key-only SSH with
  no root login through a drop-in that wins over cloud-init's, an `admin` user and a `deploy` user whose single key is
  `restrict,command="/usr/local/bin/relay-deploy"`, `ufw` on 22 (limited), 80 and 443, automatic security updates for Ubuntu and
  Caddy with a 04:30 reboot, docker, Caddy from its signed repository with the key checked against a pinned fingerprint, and the
  owner secret made once and never shown. Run for real in an Ubuntu 24.04 container (`make test-bootstrap`) with a real sshd.
  `BOOTSTRAP_SKIP_SERVICES=1` leaves `ufw`, systemd and the ssh reload out for that test only. **Not run on a real Droplet.**
- Not built: GitHub and Google sign-in and a room per PC from the dashboard (CUT-145), onboarding (CUT-146), the abuse switch,
  per-account quotas and the daily byte cap (CUT-147), account deletion and the terms (CUT-148). The usage table is filled but
  nothing refuses at a cap yet.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep the open relay and add accounts later | Rooms would be claimed by connection order in production, and every later change breaks daemons already registered. |
| A room claimed by a daemon connecting first, owner attached afterwards | A stranger could take a room id before its owner. The account has to exist first. |
| Require a sign-in on the phone to join | Protects no data (the daemon's pairing does), and breaks a QR link scanned on a phone with no session. |
| Link identities by matching email | Account takeover through an unverified email at one provider. |
| Passkeys or passwords of our own | A recovery and reset flow to build and defend. GitHub and Google are what these users already have. |
| Postgres from the start | A second service to run for one process on one Droplet. The schema moves unchanged. |
| An admin web page for suspension | An attackable surface for a task the operator does rarely, from a shell. |
| Per-room rather than per-account byte caps | A person could open more rooms to get more bandwidth. |

## Consequences

- The relay stops being a stateless pipe: it has a database, a cookie session and a sign-in, and so a backup, a rotation of
  its own secrets and an incident path. That is the cost of the service being public.
- A daemon needs a registration token before the hosted relay will take it, so self-hosting on a tailnet (no accounts) must
  keep working. The no-account mode is a configuration of the same code with sign-in off and one implicit owner, and it must
  never be the default on the hosted image.
- A pairing link made against the old open relay does not work on the hosted one. Nothing public exists yet, so nothing is lost.
- The numbers in the limits table are guesses until there is traffic.

## Not decided here

- Billing, plans and anything paid.
- Teams or sharing a room between accounts. A room has one owner.
- Email sent by the service (invites, notices). The first version sends none and shows invites as links the operator hands over.
- Whether the daemon should later be its own relay endpoint (ADR-0041, point 4).
- Several instances of the relay.
