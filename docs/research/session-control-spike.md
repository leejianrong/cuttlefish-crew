# Session control spike (CUT-91), in progress

Hands-on checks behind ADR-0032. This page holds the results so far. Checks 1, 3 and 4 are still open.

## Check 2: can two clients drive one Codex thread? (done, 2026-10-16)

Yes, on the `ws://` transport of a plain `codex app-server --listen ws://127.0.0.1:PORT`.

- `codex-cli 0.155.1`, ChatGPT login, tiny turns, one run. A 60-line Python client opened two websocket
  connections (A and B) to the one server. Both sent `initialize` then `initialized`.
- A did `thread/start` and a turn. B then did `thread/resume` on the same thread id while A was still connected.
  It succeeded, and B received that thread's notifications from then on.
- B started a turn that ran `sleep 20`. A received the same `thread/status/changed`, `turn/started`, `item/*`,
  `item/agentMessage/delta`, `thread/tokenUsage/updated` and `turn/completed` stream as B (39 and 41 events).
- Six seconds into B's turn, A called `turn/steer` with B's turn id as `expectedTurnId`. It was accepted and returned
  that turn id. Not checked: whether the steer text showed up in the final answer, and whether the earlier context
  (the remembered word) survived the resume. Check both before relying on them.
- Status events are broadcast, not per client: a `thread/status/changed` to `active` reached both clients when either
  started a turn.

What it means for CUT-92:

- The dashboard can hold a long-lived app-server and a thread per project, and call `thread/resume` then `turn/start`
  for each prompt, instead of `thread/start` every round. A person's TUI (`codex --remote ws://...`) can sit on
  the same thread.
- `turn/steer` gives mid-turn steering without waiting for a round boundary, which ADR-0008 had to accept.
- Open for the build: server-to-client approval requests. With two clients on one thread, it is not yet known which
  client receives `item/commandExecution/requestApproval`, or whether both do. Test this before shipping inline
  approvals on a shared thread.
- The `ws://` listener binds to loopback only. A phone needs the private network in front of it (ADR-0013), not a
  public bind.

## Checks 1, 3 and 4

Not run. Checks 1 and 3 need `claude` run once interactively in `.spike-scratch` to accept the folder-trust prompt.
Check 4 (the Codex desktop app) needs the app.
