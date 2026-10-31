# ADR-0050: The manager credential is its own token, held to the Approve tier and to a short route list

- Status: Accepted
- Date: 2026-10-31
- Deciders: the development agent for CUT-231, open to review by Jian

## Context

ADR-0046 says the manager agent reaches cuttlefish only through MCP tools at the Approve device tier and cannot reach Admin
routes. The MCP server is a client of the daemon that forwards one token (ADR-0020). Device tiers (ADR-0039) belong to
paired devices that come in through the relay; the PC's own token is the owner and has no tier. Something has to say what
the manager's token is.

## Decision

1. **A third credential, next to the owner's token and the hook token.** `~/.cuttlefish/manager-token` (mode 0600, kept
   across restarts, delete it to rotate), made by the daemon the same way the hook token is. `cuttlefish mcp --manager`
   reads it and ignores `--token`, so a manager server is never started with the owner's token by mistake.
2. **The daemon holds it to a tier it already has.** The middleware checks the token before the owner check and gives the
   caller the Approve tier. It then applies the same `ROUTE_TIERS` table every device is held to. No parallel auth system and
   no new tier.
3. **And to a route list.** Approve alone also lets a caller revoke a paired device (ADR-0039 chose that so a lost phone can
   be cut off from another phone), list devices, and start or stop a team. The manager does not need those. `MANAGER_ROUTES`
   in `fleet/tiers.py` names the eight routes its tools call; anything else is a 403. A test checks that every route on the
   list needs Approve or less.
4. **Journal and answer rules live in the broker and the daemon, not in the MCP tools**, so a manager that calls the HTTP API
   directly gets the same rules. `RequestResolved` gains `tier` (and `by` gains `manager`); the owner's session journals
   `owner`, a device its tier, the manager `approve`. The manager may answer `allow_once` or `deny` and nothing else. The
   never-allowed check runs inside `RequestBroker.answer` for every answerer: a command on the list is resolved `denied`
   with the reason in `text`, whoever said allow.
5. **A decision** is a `RequestRaised` of kind `decision` with `options` (two to four) and `recommended`, answered by a
   person with one of the options. It has no deadline and nothing picks an option on a timer, so the ADR-0046 deadline
   clause is left out for now. The manager cannot answer one.
6. **The manager's own session** is named by `CUTTLEFISH_MANAGER_PROJECT` (CUT-137 will set it). The manager credential is
   refused an answer or a steer there.

## Consequences

- A manager token that leaks can read digests and the attention queue, allow a command once, steer an agent and raise a
  decision. It cannot change policy, modes, secrets or devices, and cannot make a rule that outlives the answer.
- `manager-token` is a secret: AGENTS.md lists it beside the others.
- A decision is raised in the project's agent-session journal, so a daemon restart abandons it and a session stop cancels it,
  like the session's other cards. Restoring it across a restart, as blocked requests are, is left.
- "Assigned to" a project is not modelled: the manager may act on any project except its own until CUT-137 says which it
  manages.
