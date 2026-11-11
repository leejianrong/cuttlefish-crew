# ADR-0057: A worker machine dials the controller, pairs once, and holds a lease, never the model key

- Status: Accepted by Jian, 2026-11-12 (CUT-E25 holds the build; CUT-205 built the join, the registry and the heartbeat, 2026-11-12; leases and placement are not built)
- Date: 2026-11-07
- Deciders: Jian (accepted 2026-11-12), development agent for CUT-204

## Context

`docs/plans/2026-10-31-autonomous-portfolio-vision.md` wants several machines (a PC, a laptop, a remote VM) on one
dashboard, each running the projects placed on it. The relay (ADR-0037, ADR-0043) joins a browser to one daemon. A
daemon-to-worker link is a new path, and it must not undo what exists: pairing needs a confirm on the owner's PC and a
tier (ADR-0039), and an agent never holds its model key (ADR-0031).

## Decision

1. **The worker dials the controller.** The controller is the fleet daemon the person already runs. A worker is a
   `cuttlefish worker` process that opens an outbound connection to the controller's address (a tailnet address, or a
   room on the relay) and keeps it open. The controller never connects in, so a laptop behind NAT or a cloud VM with no
   public port works. The channel is the one ADR-0037 defines (end-to-end encrypted, trusted only after the first message
   decrypts); the worker reuses `cuttlefish.relay.protocol` and adds no second protocol.
2. **Pairing is ADR-0039's, with a new kind.** `cuttlefish worker join` shows a short code; the owner confirms it on the
   PC. A worker the owner has not confirmed is served nothing. The worker then holds **its own keypair and a per-worker
   secret**, and nothing else durable. The controller keeps the public half in a worker registry (a SQLite file beside
   `devices.db`, mode 0600).
3. **A worker has its own tier, below View.** It may fetch the work placed on it, post journal events, and ask for a
   lease. It may not read other projects, secrets it was not sent, settings or the device list. Every route it uses is
   listed in `fleet/tiers.py` (unlisted means Admin, so a missing entry fails safe).
4. **What a worker may hold.**
   - Its keypair and per-worker secret.
   - Project files for projects placed on it (the checkout is the work).
   - Per task, the project's own secrets, sent over the channel and kept in memory, never written to disk by cuttlefish
     (ADR-0006).
   - A **short-lived lease token** for model calls. The worker's agent is given a loopback base URL that forwards over the
     channel to the controller's broker, which adds the real key (ADR-0031). The real key is never sent to a worker.
5. **What the controller holds.** The real model keys, the broker, the secrets store, the registry and the episodic
   journal. Workers stream events in; the journal stays one store on the controller (ADR-0004), redacted at write time.
6. **Revocation.** Removing a worker (Admin tier) deletes its registry row, closes its live channel at once, revokes its
   open leases, and makes the next handshake fail. Its projects show as unplaced in Needs you. The checkout and any secret
   already in a process on that machine are the machine owner's to wipe, which is why the key is leased, not sent, and a
   lease expires in minutes without the controller's say-so. A worker silent past a timeout is treated as offline and its
   leases are not renewed.
7. **The never-allowed list and permission modes apply on a worker unchanged**: one shared constant, enforced on the worker
   and again on the controller's decision path (V4, ADR-0046).

## Alternatives considered

| Option | Why not |
|--------|---------|
| The controller dials each worker over SSH. | Needs an inbound port and a key per machine, fails behind NAT, and gives the controller a shell on every box. |
| Each worker runs a full daemon, federated. | Several journals, secrets stores and brokers; "one dashboard" becomes a sync problem (ADR-0004). |
| The hosted relay schedules workers. | The hosted relay holds no project data (ADR-0043); making it a scheduler changes what it is. |
| Send the model key to the worker and rely on the OS. | Defeats ADR-0031: one `printenv` on a cloud VM leaks it. |
| Reuse the device tiers for workers. | A worker is a machine running agents, not a person; it needs a narrower set than any device tier. |

## Consequences

- Nothing here is built. Follow-up cards: the registry and `worker join`; the forwarded broker; placing a project on a
  worker; a Workers screen with remove.
- A worker is trusted with its projects' code and secrets while it runs a task. The join screen says so.
- A controller that is down stops every worker's model calls, since leases come only from it. Accepted: it is the price of
  keeping the key off every other machine.
