"""Per-task-tree configuration for cuttlefish's satay tasks (ADR-0009, Q49).

A satay task's arguments and return value are durably journaled (satay's own
``journal.codec``), so a shared resource that isn't itself serialisable data — the
episodic store, the LLM provider, which kopicode binary to shell out to — can't be
passed as a task argument. It's configured once here, before a task's workflow is
started, the same way an application configures a database connection pool once at
process startup — except that "process startup" is no longer the only caller: the
fleet daemon (`cuttlefish.fleet`) drives several projects' teams concurrently in one
process, each needing its own `Runtime` (its own episodic store, secrets store,
backend selection). A plain module global can't hold more than one value at a time;
a `contextvars.ContextVar` can, because `asyncio.create_task()` copies the calling
context, so a task that calls `configure()` before spawning any of its own nested
tasks (exactly what `cuttlefish.fleet`'s per-project launch already does) scopes
every task nested under it, without leaking into a sibling project's task. A single
`cuttlefish run`/`run-team` process calling `configure()` once at startup, exactly as
every prior slice did, is unaffected — `ContextVar.set`/`.get` at the top of one
linear flow behaves identically to the plain global it replaces.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass

from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.llm.provider import LlmProvider
from cuttlefish.requests import RequestContext
from cuttlefish.sandbox.provider import SandboxProvider
from cuttlefish.secrets.store import SecretsStore


@dataclass(frozen=True, slots=True)
class Runtime:
    """The resources cuttlefish's tasks read when they run.

    ``sandbox_provider`` is ``None`` by default (docs/SLICES.md V2 step 2,
    KAN-1010): a task then runs against a bare scratch checkout exactly as V1
    always did, the named exception ADR-0002's addendum already accepts, not a
    new default this project is quietly widening. Configuring one is opt-in
    (``cuttlefish.cli``'s ``CUTTLEFISH_SANDBOX`` environment variable).

    ``agent_backend`` names which :class:`~cuttlefish.agents.backend.AgentBackend`
    a delegation runs through (ADR-0005) — ``"kopicode"`` by default, matching
    V1/V2's only backend, so existing callers that never set this field keep
    their exact prior behaviour. ``kopicode_binary``/``claude_code_binary``/
    ``codex_binary`` are all always present regardless of which backend is
    actually selected, the same way ``kopicode_binary`` was always required
    even before a second backend existed to compare it against.

    ``secrets_store`` is ``None`` by default (ADR-0006), mirroring
    ``sandbox_provider``'s own "opt-in, not the default" posture: an operator
    who never sets ``CUTTLEFISH_SECRETS_KEY`` gets today's exact V1/V2
    behaviour, every credential still resolved from ``os.environ`` by each
    backend's own ``_credential_envs``.

    ``requests`` is ``None`` outside the fleet daemon (``cuttlefish run`` has no inbox, so a
    command nothing approves is refused at once, as before); the daemon sets it so a kopicode
    delegation can ask a person (ADR-0028).
    """

    episodic_store: EpisodicStore
    llm_provider: LlmProvider
    kopicode_binary: str
    claude_code_binary: str = "claude"
    codex_binary: str = "codex"
    agent_backend: str = "kopicode"
    sandbox_provider: SandboxProvider | None = None
    secrets_store: SecretsStore | None = None
    requests: RequestContext | None = None


_runtime: contextvars.ContextVar[Runtime | None] = contextvars.ContextVar(
    "cuttlefish_runtime", default=None
)


def configure(runtime: Runtime) -> None:
    """Set the calling task tree's runtime. Call once, before starting any workflow —
    and, for the fleet daemon, before spawning that project's own `asyncio.create_task`
    (its context is copied at creation time, inheriting whatever was set here)."""
    _runtime.set(runtime)


def current() -> Runtime:
    """The configured runtime. Raises if `configure` was never called on this task's
    own context (or an ancestor task's, per `asyncio`'s context-copy-at-creation rule)."""
    runtime = _runtime.get()
    if runtime is None:
        raise RuntimeError("cuttlefish.runtime.configure() must be called before running a task")
    return runtime


def reset() -> None:
    """Clear the configured runtime — test-only, so one test's config can't leak."""
    _runtime.set(None)
