# ADR-0006: Project-scoped secrets are an encrypted-at-rest store, injected at delegation time — no broker yet

- Status: Accepted
- Date: 2026-09-20
- Deciders: Jian

## Context

Slice A made the coding-agent backend pluggable but left credential handling
exactly where V1/V2 left it: `KopicodeBackend`/`ClaudeCodeBackend`'s own
`_credential_envs()` functions read `OPENROUTER_API_KEY`/`ANTHROPIC_API_KEY`
straight out of `os.environ` and forward them into a sandbox verbatim
(docs/QUESTIONS.md Q11, Q22). That was fine when there was one operator, one
project, and one ambient credential. It stops being fine the moment
cuttlefish-crew's own multi-project shape is real: two projects each needing
their own GitHub token, a HuggingFace token only one of them should ever see,
a personal OpenRouter key meant to be shared — none of that fits "whatever
happens to be in this process's environment," and an operator working around
it by exporting and re-exporting different env vars per run is exactly the ad
hoc pain Q34 named.

Q34 already decided the shape and the boundary: an encrypted-at-rest store,
scoped per project with an explicit shared scope, injected at sandbox/backend
creation time through the same declared-policy mechanism already gating
command/path access — and, just as importantly, *not* a credential
broker/proxy yet (the agent never seeing a raw key at all). That deferral
isn't relaxed here; a broker is real future work once this simpler version's
gaps are concretely felt, the same discipline ADR-0002 already uses against
building ahead of a proven need.

## Decision

**Secrets are name/value pairs in a small encrypted-at-rest SQLite store
(`cuttlefish.secrets.SecretsStore`, `.cuttlefish/secrets.db` — its own file,
never a table inside `episodic.db` or satay's own `.satay/` store, the exact
discipline ADR-0004 already holds for episodic memory, extended here rather
than special-cased.** Every value is encrypted with Fernet (symmetric,
authenticated) under a key the operator holds
(`CUTTLEFISH_SECRETS_KEY`, generated via `cuttlefish secrets generate-key`)
— never derived from, or dependent on, any credential the store itself might
one day hold.

Each secret is written under a `scope`: a project's own name, or the shared
scope (`SHARED_SCOPE = "*"`) for a value like a personal OpenRouter key that
every project should be able to read. Resolving a name checks the project's
own scope first, then falls back to the shared scope — an explicit,
project-owned secret always wins over an ambient shared one, never the
reverse.

**There is no formal `Project` entity yet** (that's slice D's job, per
`docs/SLICES.md`'s stub). A project's scope is a plain string the operator
names via `cuttlefish run --project NAME`, defaulting to `--root`'s own
directory name when omitted — a real, if provisional, identifier rather than
a placeholder that blocks this slice on an entity that doesn't need to exist
yet (docs/QUESTIONS.md Q38).

**Injection is additive, not a replacement of today's ambient behaviour.**
`AgentBackend.delegate()` gains a `secrets: Mapping[str, str]` parameter:
whatever the caller already resolved from the store for this task's declared
names (`cuttlefish run --secret NAME`, repeatable, the same shape `--allow`
already established) plus the backend's own always-relevant credential names
(each backend now declares these itself via a `CREDENTIAL_ENV_VARS` class
constant, e.g. `KopicodeBackend`'s `OPENROUTER_API_KEY`/`ANTHROPIC_API_KEY`).
Each backend's `_credential_envs()` — the exact seam named as the thing to
replace, not bypass — now prefers a resolved secret over `os.environ` for the
same name, and falls back to `os.environs.get` when the store has nothing for
it. An operator who never configures `CUTTLEFISH_SECRETS_KEY` gets today's
exact V1/V2 behaviour, byte for byte: an unset key means no store, `secrets`
resolves to an empty mapping, and every credential comes from the ambient
environment exactly as it always has.

A declared `--secret NAME` that resolves to nothing in either scope is a
config-time error (`cuttlefish: declared secret(s) not found...`), not a
silent no-op — the same fail-closed posture Q17 already established for a
missing binary.

**Both invocation paths get it, not just the sandboxed one.** A sandboxed
delegation already had a per-call `SandboxSpec.envs` seam; a direct,
non-sandboxed delegation (`run_kopicode`/`run_claude_code`) never needed one
before, because a subprocess inheriting the parent's full `os.environ`
already covered the only two credential names that existed. A store-resolved
secret that isn't already an exported env var (a HuggingFace token, say)
needs to reach that subprocess too, so both functions now take an optional
`env: Mapping[str, str] | None`, merged as `{**os.environ, **env}` — `None`
or empty still means exactly what it always meant (full, unmodified
inheritance), so this is additive, not a behaviour change for a call that
declares nothing.

**Redaction is extended, not left with a blind spot.** `cuttlefish.episodic
.redact.Redactor` only ever knew to look up a fixed list of env-var names in
`os.environ`. A store-resolved secret never touches `os.environ` — it lives
only in a local variable inside the already-side-effecting delegation task —
so the existing redactor would silently miss it if that value ever echoed
back into a tool result the episodic journal writes. `cuttlefish.cli`'s
`_run()` resolves the same declared names against the same store *before*
constructing the `EpisodicStore`'s `Redactor`, and passes a `lookup` that
checks the resolved secrets first and `os.environ` second — so anything that
can reach a sandbox can also reach the redactor that has to catch it if it
leaks back out (docs/QUESTIONS.md Q39).

**No satay-journaled plaintext, ever.** Resolving a name to its value happens
entirely inside `delegate_to_agent_backend`, an already-`side_effect=True`
satay task — the resolved `dict[str, str]` is a local variable passed
straight into `backend.delegate()`, never returned from the task and never
passed as another task's argument. Only *names* (`secret_names: list[str]`,
`project: str`) cross a satay task boundary and get durably journaled by
satay's own store; a decrypted value never does. This is deliberate, not
incidental — satay's own write-time redaction (ADR-0029) only protects
values it can recognise from a fixed env-var list, and a store-resolved
secret was never going to be on that list, so the only safe answer is never
letting the plaintext become a journaled task argument or return value in
the first place.

**No broker.** The agent process itself still receives the raw secret value
as a plain environment variable inside its own sandbox/subprocess, exactly as
today's kopicode/Claude Code credential already does. A future
broker/proxy — the agent holds no raw key at all, only a scoped local
proxy does — is real, named, and explicitly deferred (Q34), not attempted
here.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Add secrets as a new field inside kopicode's own declared-allowlist policy file (`cuttlefish.delegate.policy`). | That grammar is kopicode's own (`internal/permission/allowlist_file.go`) — this project doesn't own it and has no standing to extend it unilaterally, and it says nothing about Claude Code's own permission surface at all. The declared-policy *pattern* (an operator names what's allowed, per task) is what's reused, via `--secret` mirroring `--allow`; the kopicode-owned file format itself is not. |
| Store secrets in `episodic.db` alongside episodic events. | ADR-0004 already drew this line for a different reason (satay's schema) and the same reasoning applies again: a secrets store has its own lifecycle (encryption, key rotation later) that has nothing to do with an append-only event log, and mixing them would make it impossible to reason about either file's guarantees in isolation. |
| Build the credential-broker/proxy now instead of direct injection. | Explicitly out of scope per Q34 — a proxy is real future work once this simpler version's gaps are concretely felt from real use, not before. Building it now would be exactly the "ahead of a proven need" trap ADR-0002 already argues against. |
| Encrypt with a KDF-derived key from an operator password prompted at every run. | Adds real friction (a password prompt on every `cuttlefish run`) for a threat model (the SQLite file itself being read at rest) a static, generated Fernet key already addresses just as well for one operator's own machine — the trust model this whole project still runs under (ADR-0002, Q23). Revisit if/when multi-operator hosting (slice E) makes a shared machine's disk a real, different threat. |

## Consequences

An operator can now give two projects genuinely different credentials for
the same named variable (`ANTHROPIC_API_KEY`), and give every project a
shared one (a personal OpenRouter key) without exporting or re-exporting
anything by hand — the ad hoc pain Q34 named is closed for the common case.

It also costs real things, named rather than hidden: a second encrypted
SQLite file and a key the operator now has to generate, hold, and back up
themselves (`cuttlefish secrets generate-key` — losing this key means losing
every secret it protects, the same bound any at-rest encryption scheme
accepts); a small new CLI surface (`cuttlefish secrets set/get/list/delete`)
to keep in sync with the store's own schema if it ever changes; and the
redaction seam now depends on the CLI resolving the same names twice (once
for the redactor, once inside the task) rather than once — a real, if small,
duplication accepted because the alternative (threading a live secrets
resolution into the redactor's own construction path) would couple two
things — journal redaction and task execution — that have stayed
deliberately separate since ADR-0004.

The agent process itself still holds every secret it's given in the clear,
inside its own sandbox or subprocess — this ADR does not reduce that
exposure, only makes which secrets reach which project's agent an explicit,
per-project decision instead of an ambient accident. Closing that exposure is
exactly what the deferred broker/proxy is for.

> Amended 2026-10-10 (broker): the agent's own model API key can now be held by a loopback broker instead of injected
> (ADR-0031, `CUTTLEFISH_CREDENTIAL_BROKER=1`); a project's own secrets are still injected.

> Amended 2026-10-10: the fleet daemon keeps its secrets in one central `~/.cuttlefish/secrets.db`, not one file per
> project folder, because a "shared" secret in a per-folder file is shared with nothing. It reads the folder's own
> `.cuttlefish/secrets.db` after it, when there is one. A daemon-started team now declares every secret its project can
> use (its names, never values, ride the runtime, not a task argument). A backend's own credential is told apart from a
> project's secret by `CREDENTIAL_ENV_VARS`; what each harness can hide from its own commands is in
> `docs/research/harness-credentials-spike.md`. Also fixed: only a backend's credential names were scrubbed from the
> journal, so a declared secret's value was written to it; every secret a run resolves is now scrubbed.
