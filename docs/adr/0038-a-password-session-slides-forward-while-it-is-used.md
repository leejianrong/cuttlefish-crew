# ADR-0038: A password session slides forward while it is used, up to seven days

- Status: Accepted
- Date: 2026-10-17
- Deciders: Jian

## Context

ADR-0011 made a non-loopback session last 12 hours from the password, flat. That suits a laptop. It does not suit a
phone that is used on and off for days: the session ends mid-use and the person types the password again. A
daemon restart also ends every session, because the signing secret is made per process, and until CUT-112 the page
then sat on "Lost connection to the daemon. Retrying…" with no way forward except Disconnect (checked live, CUT-98).

Jian chose, on 2026-10-17, to slide the expiry on use and to keep 12 hours of idle time.

## Decision

**A session that is in use is extended, and a session that is not in use still ends 12 hours after its last request.**

- The token is `{expiry}.{issued}.{mac}`. `issued` is when the password was typed and never changes through a refresh.
- When a request carries a valid token that is past half its life, the response carries a fresh one in
  `x-cuttlefish-token-refresh`. The fresh token expires `ttl` (12 hours) after that request, cut off at
  `issued + 7 days`, so a phone that is always in use is asked for the password again within a week.
- The dashboard keeps the newest token in `localStorage` and sends it from then on. The header is listed in the CORS
  `expose_headers`, so a page on another allowed origin can read it.
- A rejected request hands out nothing. The loopback static token is unchanged.
- The signing secret stays per process and is not persisted, so a restart still ends every session (ADR-0011's "restart
  to revoke"). The dashboard now says so: when the daemon answers 401, a **Sign in again** dialog asks for the password
  (or, in loopback mode, the new token) and the page carries on where it was. A terminal that closed because of the
  restart reconnects after that sign-in.

## Consequences

- A stolen unlocked phone stays signed in for as long as it is used, up to seven days from the last password. The
  tiers of ADR-0037 (a terminal needs a step-up) are the answer for the dangerous actions; this ADR does not add one.
- Sessions are still stateless on the server: no table of them, no way to list or revoke one except a restart. A
  device store arrives with ADR-0037.
- The websocket terminal sends the token in its first message, so it uses whichever token the client holds when it
  connects. A connection already open is not cut off when its token ages.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep 12 hours flat and only add the sign-in dialog | The password is typed twice a day on a phone, which Jian did not want. |
| Remember the password in the browser and sign in by itself | It leaves the password in browser storage. |
| Persist the signing secret so a restart keeps sessions | It removes "restart to revoke", the only revocation there is. A person who wants it can ask for it later. |
| A refresh endpoint called on a timer | One more route and a timer that can fail quietly. A header on responses already being made needs neither. |

## Checked

Unit and integration tests cover the refresh, the cap, a forged token, a rejected request and the CORS header. Live: a
daemon with a 20 second session, a phone-width browser in use for 40 seconds stayed signed in and stored a new token; a
restart showed the dialog and a wrong password was refused.
