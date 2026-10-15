# ADR-0033: A Codex turn in flight takes a steer through `turn/steer`

- Status: Accepted
- Date: 2026-10-16
- Deciders: Jian

## Context

ADR-0008 made steering a round-boundary redirect because no backend's headless surface took input after it
started. ADR-0008 also named the way out: "a backend's own live-input capability (if it ever supports one) is what
would license anything more". Codex's `app-server` has one. `turn/steer` adds a message to the turn in flight.
It was checked live (`docs/research/session-control-spike.md`, CUT-91 check 2): the steer was accepted from a second
client, and its text reached the final answer. ADR-0032 makes a continuous, steerable session the primary object.

## Decision

**A steer sent through the fleet daemon goes into a running Codex turn at once; every other case keeps the
round-boundary behaviour of ADR-0008.**

- While a Codex delegation's turn runs, `run_codex_app_server` registers it by project root and role
  (`cuttlefish.delegate.codex_live`). The daemon shares the process with the delegation (ADR-0009), so
  `FleetDaemon.steer` asks the registry first.
- Accepted: the message is journaled as `SteeringQueued` and `SteeringMessage` together, so the dashboard shows it
  as applied now, and nothing is queued in satay (it would apply twice).
- Not accepted (no turn running, another backend, Codex refused, or the turn ended in the race): the message is
  queued for the round boundary exactly as before.
- `cuttlefish steer` (the CLI, through satay's control API) is unchanged and still lands at a round boundary.
- No new protocol: this is Codex's own `turn/steer`, sent by the connection that already owns the turn (ADR-0005).

## Consequences

- ADR-0008 still holds for kopicode and Claude Code until their own live input is verified (CUT-93, CUT-94).
- The registry is in-process. A daemon restart loses it, which is right: the turn died with the process.
- Codex may fold the steer into the turn, so the agent can act on it after a command it had already started.
  It is a nudge, not an interrupt. Stop is still `turn/interrupt`.
