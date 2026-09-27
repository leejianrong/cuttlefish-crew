# ADR-0018: Codex is a third `AgentBackend` — a coarser sandbox-tier policy mapping, a stderr-heuristic refusal signal, and a fully inert ambient credential, each named rather than glossed over

- Status: Accepted
- Date: 2026-09-28
- Deciders: Jian

## Context

KAN-1713 (CUT-E10): "Proves the pluggable AgentBackend seam (ADR-0005)
generalizes a second time, the same way ClaudeCodeBackend proved it the
first time against kopicode's own shape. Bounded, known-shape work — not a
redesign." Unlike kopicode (Jian's own project, source read directly) and
Claude Code (closed-source, but its own `--output-format stream-json`
surface was verified live in ADR-0005), Codex is both closed-source *and* a
CLI this project had never invoked before — every fact below was verified
live against the real `codex` binary (2026-09-28, `codex-cli` 0.155.1,
logged in via ChatGPT), never assumed from documentation or inferred from
the other two backends' own shape.

Three real findings changed the design from what a straight port of
`ClaudeCodeBackend`'s own shape would have produced:

**1. Codex's own permission model is coarser than either existing backend
can approximate well.** kopicode's declared-allowlist and Claude Code's
`--allowedTools`/`--disallowedTools` both name specific commands. `codex
exec --sandbox {read-only,workspace-write,danger-full-access}` is a
three-tier OS sandbox policy with no per-command filtering at all —
confirmed against `codex exec --help`, there is no flag remotely resembling
a command list. Mapping cuttlefish's own `allow: list[list[str]]` onto this
can only be binary: nothing declared → `read-only` (the fail-closed default
every backend already holds to); anything declared at all → `workspace-write`
(the only tier broad enough to let *any* declared command run), regardless
of which commands were actually named. This grants broader access than an
operator naming one specific command may have intended — a real, open gap,
the same shape as (but coarser than) Claude Code's own already-accepted
`--allowedTools` approximation (Q36).

**2. A permission denial has no structured signal on Codex's own event
stream at all.** Verified live: asking Codex to write a file under
`--sandbox read-only` produces no `item.completed` of any "denied"/"error"
shape — the turn still ends in an ordinary `turn.completed` (the model
gracefully explains it couldn't do the task), and the *only* trace that
anything was declined is an unstructured Rust log line on stderr
(`ERROR codex_core::tools::router: error=patch rejected: ... rejected by
user approval settings`), with the process still exiting `0`. Neither
kopicode's `permission_decided`/`deny` event nor Claude Code's
`permission_denials` array has an analogue here. `classify_stream` falls
back to a substring check (`"rejected"`) on stderr, folded in only when no
edit landed — an honest heuristic on an internal, undocumented log message
that could reword itself in a future Codex release without notice, named as
exactly that in the module's own doc comment, not asserted as a stable
contract.

A genuine, structured failure *does* exist and is used instead wherever
possible: an invalid model name (probed live) produced a real
`{"type":"turn.failed","error":{"message":"..."}}` event and exit code `1` —
this is `DelegationOutcome(kind="failed", ...)`, the same clean signal both
other backends already have for their own real failures.

**3. Codex's headless surface does not read `OPENAI_API_KEY` as an ambient
credential at invocation time — verified live, and a real, load-bearing
finding, not assumed.** With no `~/.codex/auth.json` present and a
syntactically valid (if fake) `OPENAI_API_KEY` set in the environment, `codex
exec` still produced the identical `401 Unauthorized: Missing bearer or
basic authentication in header` as with no key set at all — no
`Authorization` header was ever attached to the outbound request either
way. Only a persisted `~/.codex/auth.json` (written by `codex login`, either
the interactive ChatGPT OAuth flow or `codex login --with-api-key` reading a
key from stdin once) authenticates a real invocation. This is a strictly
harder version of `ClaudeCodeBackend`'s own already-accepted OAuth gap
(Q37): Claude Code at least honors `ANTHROPIC_API_KEY` as a genuine ambient
env var when one is set; Codex, as verified here, does not honor its
equivalent at all.

## Decision

**`CodexBackend` (`cuttlefish.agents.codex`) and its delegation mechanics
(`cuttlefish.delegate.codex`) mirror `ClaudeCodeBackend`'s own shape exactly**
— `build_codex_argv`/`classify_stream`/`run_codex`/`run_codex_in_sandbox`/
`classify_codex_output`/`parse_stream_json`, the identical split every
backend's own delegate module already makes. `codex exec --json
--skip-git-repo-check --sandbox <read-only|workspace-write> task_text` is
the full argv; `--skip-git-repo-check` is load-bearing, not decorative —
verified live that Codex otherwise refuses outright ("Not inside a trusted
directory") against a plain scratch checkout that isn't a git repository,
exactly the kind of root V1's own original delegation already runs against.

**`DelegationOutcome.tokens` sums `turn.completed`'s own `usage.input_tokens`
+ `usage.output_tokens` only** — `cached_input_tokens`/
`cache_write_input_tokens`/`reasoning_output_tokens` are deliberately not
added on top. Verified live across every probe: `cached_input_tokens` was
always smaller than `input_tokens`, consistent with a subset/breakdown
relationship (how many of the input tokens were cache hits, for billing
purposes) rather than Claude's own genuinely-additive cache-token pools.
Summing them too would silently overcount — this reading of the live
numbers is a defensible inference, not an independently confirmed fact (no
public Codex source or usage-schema doc exists to check against), and is
named as such in the module's own doc comment.

**`DelegationOutcome.cost_usd` is always `None` for this backend** — Codex's
own `turn.completed` reports token usage but never a dollar figure, verified
live against a real authenticated run. The identical honest gap
`cuttlefish.delegate.kopicode` already documents (ADR-0017), for the
identical reason: no fabricated pricing-table estimate, an honestly-absent
number instead.

**`CodexBackend.CREDENTIAL_ENV_VARS = ("OPENAI_API_KEY",)`** — forwarded the
same way every other backend's ambient credential is (harmless, and
future-proof if Codex's own auth precedence ever changes), even though
verified live to be currently inert for `codex exec` itself. This project's
sandboxed-credential-forwarding mechanism (ADR-0006) is consequently inert
for this backend: `CodexBackend._delegate_inside_sandbox` mounts the binary
and `root` exactly like the other two backends do, but a sandboxed
invocation reliably fails closed on a `401`, not a silent success — the same
"real, accepted gap for this slice, not solved" posture
`cuttlefish.agents.claude_code`'s own OAuth gap already holds (Q37), just
via a different credential file (`~/.codex/auth.json`) than an env var.
Mounting the operator's own live `~/.codex` session directory into a sandbox
to make this actually work was considered and explicitly **not** done —
that directory is a full, revocable-only-by-logout account session, a
materially broader and more sensitive thing to expose to an untrusted
sandbox than a scoped, single-purpose `SecretsStore` credential (ADR-0006's
own least-privilege reasoning), and deciding to widen that exposure is a
call for a future ADR if a real need for sandboxed Codex delegation shows
up, not a quiet default here.

**Selection**: `CUTTLEFISH_AGENT_BACKEND=kopicode|claude-code|codex`
(`cuttlefish.config.resolve_agent_backend`), `CUTTLEFISH_CODEX_BIN` (default
`codex`) mirroring `CUTTLEFISH_CLAUDE_CODE_BIN`'s own pattern exactly.
`cuttlefish.runtime.Runtime`/`cuttlefish.config.PreparedRun` both gain
`codex_binary: str = "codex"`; `cuttlefish.agents.registry.resolve_backend`
gains a third branch.

**Testing**: `requires_codex`/`requires_codex_live` mirror
`requires_claude_code`/`requires_claude_code_live` exactly (binary-on-PATH
free skip vs. an explicit `CUTTLEFISH_TEST_CODEX_LIVE=1` opt-in for anything
that actually costs money) — for the identical reason: Codex's own headless
auth is a persisted session, not an environment variable, so presence can't
be inferred from env state the way kopicode's credential can.
`tests/unit/delegate/test_codex_classify.py`/`test_codex_sandbox.py` cover
the pure reduction logic and the sandbox seam against synthetic events, the
same split every other backend's own test suite already makes;
`tests/integration/delegate/test_codex_real.py` proves a real edit lands and
a real refusal is correctly classified, run live against the real binary
during this slice (both passed) before merging.

## Alternatives considered

| Option | Why not |
|--------|---------|
| Map any declared `allow` entry to `danger-full-access` instead of `workspace-write` | `workspace-write` already lets every declared-allowlist use case this project has today (file edits, running the operator's own build/test commands inside `root`) succeed — reaching for full-disk access on top would grant strictly more than any other backend's own declared allowlist ever does, widening the gap named above rather than merely accepting it. |
| Treat every "no edit landed" outcome as `"refused"` outright, skip the stderr heuristic entirely | Would misclassify the extremely common "genuinely nothing to do" case (verified live: a plain "say hi" task with no edit requested also produces zero edited paths) as a permission failure, breaking the identical "nothing to do is still `completed`" precedent both other backends already hold to. |
| Mount the operator's own `~/.codex` directory into the sandbox so sandboxed delegation actually authenticates | A real option, but a materially bigger exposure decision (a live, revocable-by-logout-only account session, not a scoped secret) than this bounded card's own "known-shape work, not a redesign" framing calls for — deferred to a future ADR if sandboxed Codex delegation becomes a real, proven need. |
| Skip live-testing the refusal/failure paths, ship only the "happy path proven live, everything else reasoned by analogy" version | Every other backend's own real-refusal and real-failure shape was individually verified live before being trusted (ADR-0003/0005's own standing discipline) — Codex's own refusal signal in particular (no structured event, only a log line) could not have been discovered any other way, and shipping a *guessed* classification here risked a silent misclassification exactly the kind this project's whole culture exists to avoid. |

## Consequences

Backend selection remains process-wide via `CUTTLEFISH_AGENT_BACKEND` —
neither `run` nor `run-team` has ever had a per-invocation flag for it, and
this ADR doesn't add one, unchanged from ADR-0005's own shape, now with a
third valid value. An operator
authenticated into Codex via ChatGPT (this build's own case, and presumably
the common one for an individual developer) gets a fully working host-direct
backend today; a project needing sandboxed delegation must still use
kopicode or Claude Code (with its own already-accepted, narrower credential
gap) until sandboxed Codex auth gets its own decision. The declared-allowlist
policy story is now honestly three-tiered in fidelity — kopicode's own
purpose-built command-level gate, Claude Code's pattern-based approximation,
Codex's binary read-only/workspace-write approximation — matching
ADR-0005's own Consequences section, which named exactly this divergence as
an open, expected question the moment a second backend existed, not a
surprise this ADR is walking back from.
