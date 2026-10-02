# ADR-0021: kopicode is driven over `kopicode serve`, and cuttlefish is its live consent client — a resident child, a session per delegation, deny by default, no containment claim

- Status: Proposed
- Date: 2026-09-29
- Deciders: Jian

## Context

Two problems surfaced integration-testing the `run --print` seam.

**A declared shell allowlist can't express what a model actually types.** kopicode's
shell tool always runs `/bin/sh -c "<line>"`, and its `--policy-file` allowlist
exact-matches that whole argv. A model that phrases `uv run pytest` three ways is refused
three times (kopicode issue #157). kopicode has rejected loosening the match three times —
a prefix match that allows `uv run pytest` also allows `uv run pytest && rm -rf /` — and
won't be patched. Its answer is a live consent mode on `kopicode serve` (kopicode ADR-0016,
PR #159): every shell command or out-of-root write is asked of the client, per action.

**Failures looked identical.** A failed round read `exit_code=3 reason=error`. The real
diagnostic — provider HTTP status and body, the harness error — is `session_ended.text`.

## Decision

**1. `KopicodeBackend` uses `kopicode serve`, in a sandbox as well as out of one**
(`cuttlefish.delegate.kopicode_serve`), `session.start` with `consent_mode:
"remote_interactive"`.

- *No sandbox:* a resident child, as decision 2 describes.
- *A sandbox whose provider can stream* (`cuttlefish.sandbox.provider.StreamingSandboxProvider`,
  currently `ContainerSandboxProvider`, whose `spawn` is `docker exec -i`): one fresh sandbox
  and one `serve` child **per delegation**, under the same `ConsentPolicy`, torn down by
  `destroy()` afterwards. No policy file is mounted; consent is live, and each decision is
  journaled as in the unsandboxed case.
- *A sandbox whose provider cannot stream* (E2B): `run --print` inside the sandbox with the
  declared-allowlist policy file, as before. `SandboxProvider.exec` returns only after the
  process exits, so it cannot host a stdio child.
- `transport="print"` forces the old path everywhere.

The streaming capability is a **separate, optional interface**, not a method every provider
must have, so E2B keeps working unchanged and can opt in later. `ServeChild` accepts an
already-started process (`run_kopicode_serve(process_factory=...)`), so the reader loop,
consent handling and `session.close` are the same code in both places.

*Why per delegation, not a resident container:* ending the local `docker exec -i` client does
**not** stop the command inside the container (checked on Docker 29.2.1: a SIGKILLed client
left its process running). A surviving `kopicode serve` would keep the working-tree lock and
refuse the next session with `-32005`. `destroy()` is the one cleanup that does not depend on
the process cooperating, so each delegation gets its own container and always destroys it. A
pooled container is possible later, but it would need its own answer to that orphan.

*Cost, measured* (`scripts/measure_sandbox_latency.py`, 15 runs, WSL2, an invalid key so no
model time and one real 401 round trip in every arm): a delegation with nothing to do took a
median of **131 ms** on the host with a fresh child, **99 ms** with a pooled child, and
**617 ms** in a container (create 214, session 196, destroy 207). The container adds roughly
half a second per delegation, small beside a model turn, and accepted for sandboxed runs.

The sandbox does not loosen consent: a sandboxed role gets the same rules as an unsandboxed
one. A broader sandbox-only policy would be a separate decision with its own ADR.

**2. A resident `serve` child, one session per delegation, closed with `session.close`.**
A child is kept per (binary, credential set) — it reads its environment once, so different
projects' secrets cannot share one (`ServePool`). Each delegation is its own session, so one
role's conversation never reaches the next, and it ends with kopicode's `session.close`
(v0.2.0, kopicode PR #161): that writes the session's `session_ended` — the only event whose
`text` carries the failure — releases the working-tree lock (a second session on the same
root is refused with `-32005` until then) and frees the id, without ending the process. A
close that cannot be confirmed kills the child rather than leaving a lock held; the next
delegation respawns one. A child bound to a finished event loop, or one that has died, is
replaced.

*History:* the first version of this ADR spawned one child per delegation because kopicode
had no `session.close` — `session_ended` and the lock were only released by ending the
process. That constraint is gone; the per-delegation spawn is now only the path taken when
no pool is given.

**3. The consent policy** (`cuttlefish.delegate.consent`) is built from a role's declared
`allow` — a role with none (the default, and every read-only role) gets no shell at all.
`detail` is untrusted model output; every rule fails closed:

- `write_outside_root`: always denied, for every role. Writes inside `dir` never reach consent.
- `run_shell`: the line must be at most 1 KiB and consist only of words of `[A-Za-z0-9_.,:=@%+/-]`
  joined by single spaces — no quote, `; & | < > $ ` ( ) { } \ * ? ~ #`, newline or tab. Only
  then is it matched by argv **prefix** against the role's entries; with no shell syntax
  left in the line, `/bin/sh -c` runs exactly the words matched, so prefix matching is
  sound. (An allowlist of characters, not a denylist of metacharacters.) Arguments after
  the matched prefix may not be absolute or contain a `..` segment, including in a
  `--flag=value` value.
- `allow` entries are argv (`--allow 'uv run pytest'`) or kopicode's `["/bin/sh","-c",line]`
  shape; an entry with a metacharacter in it is refused at config time, since it could
  never match.
- Never `allow_session`: each decision is made and logged individually.
- The answer is deadline-bounded (30s) by the client, well inside kopicode's fixed 60s; a
  timeout or exception in the decider is a `deny`, a malformed request is a `deny`, and
  cancelling a delegation denies every outstanding request before `session.cancel`.

A prefix entry is a trust grant for that program's whole flag surface: `["python"]` would
allow `python -m anything`. Entries should name a full subcommand (`uv run pytest`).

**4. Failure kinds.** `DelegationOutcome.failure_kind` (new, optional) is one of
`provider_auth` (exit 3, HTTP 401/403), `provider_credits` (402), `provider_rate_limit`
(429), `provider_outage` (5xx), `provider_other`, `harness_error` (exit 4), `max_turns`,
`verification_failed`, `budget_exhausted`, `cancelled`, `open_failed` (`-32002`),
`protocol_error`. The `text` goes into `reason` after the same credential redaction as a
stderr tail. An edit that landed no longer hides a failed stop on this transport.

## Containment

**kopicode does not sandbox what the model's shell can do** (kopicode ADR-0008/0011), and
neither does this policy. Approving `uv run pytest` is a decision about which commands to
run, not a boundary around them: it still executes arbitrary repository code as this user,
with this process's environment, network and filesystem. Denying `..` and absolute
arguments narrows accidents; it is not a jail. Real containment of an unsandboxed
delegation is this project's responsibility, and today it is absent by design (ADR-0002's
one-operator, own-repo trust model). Where the boundary is needed, run the delegation with
a sandbox provider; on a streaming provider the same live consent applies inside it.

## Consequences

- Needs kopicode **v0.2.0 or later** (serve's consent mode, PR #159, and `session.close`,
  PR #161). v0.1.0 has no `serve` at all; the client reports kopicode's stderr in the
  `DelegationError`.
- Consent decisions are logged (`cuttlefish.delegate.consent`, INFO) with role-agnostic
  session id, kind, capped `detail`, answer and rule, and each one is also journaled as a
  `ConsentDecided` episodic event (KAN-1792) right after the round's `ToolCallRecorded`
  rows, redacted at write time like any episodic text. kopicode's own `permission_decided` events (`source: "remote"`) still
  feed the per-call `ToolCallRecord` status.
- A sandboxed delegation on a streaming provider no longer has #157's exact-match problem.
  One on a provider that cannot stream still does, and E2B additionally cannot run a kopicode
  delegation at all today: `create` rejects bind mounts, which the delegation needs for the
  repository and the binary.
