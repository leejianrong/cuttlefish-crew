"""Config resolution shared by every caller that starts a delegation (ADR-0009).

Originally `cuttlefish.cli`'s own private `_prepare_run`/`_PreparedRun` (factored out
once `run` and `run-team` both needed it, ADR-0007's own comment on why). Pulled out
to its own module now that a second kind of caller exists: the fleet daemon
(`cuttlefish.fleet`) resolves a `Runtime` for each project it starts a team for, the
identical resolution `cuttlefish run`/`run-team` already do, just rooted at that
project's own `root` instead of `Path.cwd()`.

``base_dir`` is where this call's ``.cuttlefish/`` lives — ``Path.cwd()`` for a
``cuttlefish run``/``run-team`` invocation (unchanged), a project's own ``root`` for
the fleet daemon (ADR-0009): every store this project has built (`secrets.db`,
`episodic.db`) stays colocated with the project being worked on, whichever caller is
asking.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from cuttlefish import runtime
from cuttlefish.agents.backend import AgentBackend
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.llm.provider import LlmProvider
from cuttlefish.sandbox.provider import SandboxProvider
from cuttlefish.secrets.store import SECRETS_KEY_ENV, InvalidSecretsKeyError, SecretsStore

KOPICODE_BIN_ENV = "CUTTLEFISH_KOPICODE_BIN"
DEFAULT_KOPICODE_BIN = "kopicode"
CLAUDE_CODE_BIN_ENV = "CUTTLEFISH_CLAUDE_CODE_BIN"
DEFAULT_CLAUDE_CODE_BIN = "claude"
CODEX_BIN_ENV = "CUTTLEFISH_CODEX_BIN"
DEFAULT_CODEX_BIN = "codex"
AGENT_BACKEND_ENV = "CUTTLEFISH_AGENT_BACKEND"
DEFAULT_AGENT_BACKEND = "kopicode"
LLM_PROVIDER_ENV = "CUTTLEFISH_LLM_PROVIDER"
DEFAULT_LLM_PROVIDER = "openrouter"
SANDBOX_ENV = "CUTTLEFISH_SANDBOX"
DEFAULT_SANDBOX = "none"


class ConfigError(Exception):
    """A startup configuration problem, checked before a task is accepted (Q17)."""


def resolve_kopicode_binary() -> str:
    return os.environ.get(KOPICODE_BIN_ENV, DEFAULT_KOPICODE_BIN)


def resolve_claude_code_binary() -> str:
    return os.environ.get(CLAUDE_CODE_BIN_ENV, DEFAULT_CLAUDE_CODE_BIN)


def resolve_codex_binary() -> str:
    return os.environ.get(CODEX_BIN_ENV, DEFAULT_CODEX_BIN)


#: How long a person has to answer a Needs-you request, in seconds (ADR-0028).
REQUEST_WINDOW_ENV = "CUTTLEFISH_REQUEST_WINDOW"
DEFAULT_REQUEST_WINDOW_S = 600.0


def resolve_request_window() -> float:
    """``CUTTLEFISH_REQUEST_WINDOW`` seconds (10 s to 24 h), default 10 minutes. The kopicode
    binary may allow less (see ``serve_supports_consent_timeout``)."""
    raw = os.environ.get(REQUEST_WINDOW_ENV)
    if raw is None:
        return DEFAULT_REQUEST_WINDOW_S
    try:
        seconds = float(raw)
    except ValueError:
        seconds = -1.0
    if not 10.0 <= seconds <= 86400.0:
        raise ConfigError(f"{REQUEST_WINDOW_ENV}={raw!r} must be a number of seconds, 10 to 86400")
    return seconds


def validate_backend_name(choice: str, *, source: str) -> str:
    if choice not in ("kopicode", "claude-code", "codex"):
        raise ConfigError(
            f"unknown {source}={choice!r}; expected 'kopicode', 'claude-code', or 'codex'"
        )
    return choice


def resolve_agent_backend(override: str | None = None) -> str:
    """Which :class:`~cuttlefish.agents.backend.AgentBackend` a delegation runs
    through (ADR-0005) — "kopicode" by default, matching V1/V2's only backend.

    ``override`` (KAN-1809) is a project's own backend; it wins over the
    process-wide ``CUTTLEFISH_AGENT_BACKEND``, which stays the default for anything
    that names none.
    """
    if override is not None:
        return validate_backend_name(override, source="backend")
    return validate_backend_name(
        os.environ.get(AGENT_BACKEND_ENV, DEFAULT_AGENT_BACKEND), source=AGENT_BACKEND_ENV
    )


def check_binary_on_path(binary: str, *, env_hint: str) -> None:
    """Fail closed, before a task is even accepted (Q17) — not discovered mid-task."""
    if shutil.which(binary) is None:
        raise ConfigError(f"{binary!r} is not on PATH. Install it, or set {env_hint} to its path.")


def resolve_llm_provider() -> LlmProvider:
    """cuttlefish's own reasoning provider (QUESTIONS.md Q11).

    "replay" is a test/debug escape hatch, not a documented operator choice: it
    answers every call with a fixed, uninformative response so a run can be
    smoke-tested with no live credential. A real run defaults to "openrouter" — one
    key over an OpenAI-compatible endpoint reaches many upstream models, rather than
    locking cuttlefish to a single vendor SDK. "claude" remains available for a
    direct Anthropic credential. The openrouter provider is built lazily, so a
    missing key surfaces (with an actionable message) only if a handover needs it.
    """
    choice = os.environ.get(LLM_PROVIDER_ENV, DEFAULT_LLM_PROVIDER)
    if choice == "openrouter":
        from cuttlefish.llm.lazy import LazyLlmProvider
        from cuttlefish.llm.openrouter import OpenRouterLlmProvider

        # Lazy (KAN-1807): the key is only needed once a handover summary is due.
        return LazyLlmProvider(OpenRouterLlmProvider)
    if choice == "claude":
        from cuttlefish.llm.claude import ClaudeLlmProvider

        return ClaudeLlmProvider()
    if choice == "replay":
        from cuttlefish.llm.provider import LlmResponse
        from cuttlefish.llm.replay import ReplayLlmProvider

        return ReplayLlmProvider(
            [LlmResponse(model="replay", text="(no real summary — replay provider)")] * 1000
        )
    raise ConfigError(
        f"unknown {LLM_PROVIDER_ENV}={choice!r}; expected 'openrouter', 'claude', or 'replay'"
    )


def resolve_sandbox_provider() -> SandboxProvider | None:
    """Real containment for the kopicode delegation (docs/SLICES.md V2 step 2,
    KAN-1010) — opt-in, not the default. "none" (unset) keeps V1's original
    behaviour: the delegation runs directly against ``root``, the named exception
    ADR-0002's addendum already accepts, not silently widened just because a
    sandbox package now exists.
    """
    choice = os.environ.get(SANDBOX_ENV, DEFAULT_SANDBOX)
    if choice == "none":
        return None
    if choice == "container":
        from cuttlefish.sandbox.container import ContainerSandboxProvider, DockerNotAvailableError

        try:
            return ContainerSandboxProvider()
        except DockerNotAvailableError as exc:
            raise ConfigError(str(exc)) from exc
    if choice == "e2b":
        from cuttlefish.sandbox.e2b import E2bSandboxProvider, MissingApiKeyError

        try:
            return E2bSandboxProvider()
        except MissingApiKeyError as exc:
            raise ConfigError(str(exc)) from exc
    raise ConfigError(f"unknown {SANDBOX_ENV}={choice!r}; expected 'none', 'container', or 'e2b'")


def secrets_db_path(base_dir: Path) -> Path:
    return base_dir / ".cuttlefish" / "secrets.db"


def resolve_secrets_store(base_dir: Path) -> SecretsStore | None:
    """Project-scoped secrets (ADR-0006) — opt-in, mirroring
    :func:`resolve_sandbox_provider`'s "none by default" posture. An operator who
    never sets ``CUTTLEFISH_SECRETS_KEY`` gets today's exact V1/V2 behaviour: no
    store, every credential still resolved from ``os.environ`` by each backend's own
    ``_credential_envs``.
    """
    if SECRETS_KEY_ENV not in os.environ:
        return None
    try:
        return SecretsStore.open(secrets_db_path(base_dir))
    except InvalidSecretsKeyError as exc:
        raise ConfigError(str(exc)) from exc


def resolve_project_secrets(
    *,
    backend: AgentBackend,
    secrets_store: SecretsStore | None,
    project: str,
    secret_names: list[str],
) -> dict[str, str]:
    """Every name this delegation should try to resolve from `secrets_store` --
    an operator's own `--secret` declarations plus whatever `backend` always tries
    ambiently (`AgentBackend.CREDENTIAL_ENV_VARS`, ADR-0006) -- resolved eagerly here
    (not just inside the task) so a missing *declared* name fails closed before a
    task is even accepted (Q17), and so the same resolved values can seed the
    episodic journal's redactor.
    """
    if secrets_store is None:
        if secret_names:
            raise ConfigError(f"--secret was given but {SECRETS_KEY_ENV} is not set")
        return {}
    names = sorted(set(secret_names) | set(backend.CREDENTIAL_ENV_VARS))
    resolved = secrets_store.resolve(project, names)
    missing = [name for name in secret_names if name not in resolved]
    if missing:
        raise ConfigError(
            f"declared secret(s) not found for project {project!r} or the shared scope: "
            + ", ".join(missing)
        )
    return resolved


@dataclass(frozen=True, slots=True)
class PreparedRun:
    """Every config seam a delegation resolves identically before starting its own
    workflow — shared by `cuttlefish.cli`'s `run`/`run-team` and `cuttlefish.fleet`
    (ADR-0007's own factoring, extended by ADR-0009 past a single CLI process)."""

    kopicode_binary: str
    claude_code_binary: str
    codex_binary: str
    agent_backend: str
    llm_provider: LlmProvider
    sandbox_provider: SandboxProvider | None
    secrets_store: SecretsStore | None
    episodic_store: EpisodicStore

    def close(self) -> None:
        self.episodic_store.close()
        if self.secrets_store is not None:
            self.secrets_store.close()

    def as_runtime(self) -> runtime.Runtime:
        return runtime.Runtime(
            episodic_store=self.episodic_store,
            llm_provider=self.llm_provider,
            kopicode_binary=self.kopicode_binary,
            claude_code_binary=self.claude_code_binary,
            codex_binary=self.codex_binary,
            agent_backend=self.agent_backend,
            sandbox_provider=self.sandbox_provider,
            secrets_store=self.secrets_store,
        )


def prepare_run(
    *,
    project: str,
    secret_names: list[str],
    base_dir: Path | None = None,
    agent_backend: str | None = None,
    extra_backends: Sequence[str] = (),
) -> PreparedRun:
    """Resolve the backend, LLM provider, sandbox, and secrets store for one
    delegation rooted at `base_dir` (default: `Path.cwd()`) -- or raise
    `ConfigError`, closing any secrets store already opened first, so a caller only
    has to report the error, no further cleanup required.

    Building the episodic store's redactor (which needs these same resolved secret
    names, Q39) is the caller's job, not this function's -- `cuttlefish.cli`'s
    `_prepare_run` and `cuttlefish.fleet`'s runtime factory each open their own
    `EpisodicStore` with their own redaction lookup, both rooted at the same
    `base_dir` this function resolved against.
    """
    resolved_base_dir = base_dir if base_dir is not None else Path.cwd()
    kopicode_binary = resolve_kopicode_binary()
    claude_code_binary = resolve_claude_code_binary()
    codex_binary = resolve_codex_binary()

    from cuttlefish.agents.registry import resolve_backend

    secrets_store = None
    try:
        agent_backend = resolve_agent_backend(agent_backend)
        # Every backend any role may name is checked now (Q17), not mid-task.
        for needed in dict.fromkeys([agent_backend, *extra_backends]):
            validate_backend_name(needed, source="backend")
            if needed == "kopicode":
                check_binary_on_path(kopicode_binary, env_hint=KOPICODE_BIN_ENV)
            elif needed == "claude-code":
                check_binary_on_path(claude_code_binary, env_hint=CLAUDE_CODE_BIN_ENV)
            else:
                check_binary_on_path(codex_binary, env_hint=CODEX_BIN_ENV)
        backend = resolve_backend(
            agent_backend,
            kopicode_binary=kopicode_binary,
            claude_code_binary=claude_code_binary,
            codex_binary=codex_binary,
        )
        llm_provider = resolve_llm_provider()
        sandbox_provider = resolve_sandbox_provider()
        secrets_store = resolve_secrets_store(resolved_base_dir)
        resolved_secrets = resolve_project_secrets(
            backend=backend,
            secrets_store=secrets_store,
            project=project,
            secret_names=secret_names,
        )
    except ConfigError:
        if secrets_store is not None:
            secrets_store.close()
        raise

    from cuttlefish.episodic.redact import DEFAULT_SECRET_ENV_VARS, Redactor

    def _redaction_lookup(name: str) -> str | None:
        return resolved_secrets.get(name) or os.environ.get(name)

    redaction_names = sorted(set(DEFAULT_SECRET_ENV_VARS) | set(backend.CREDENTIAL_ENV_VARS))
    episodic_store = EpisodicStore.open(
        resolved_base_dir / ".cuttlefish" / "episodic.db",
        redactor=Redactor(redaction_names, lookup=_redaction_lookup),
    )
    return PreparedRun(
        kopicode_binary=kopicode_binary,
        claude_code_binary=claude_code_binary,
        codex_binary=codex_binary,
        agent_backend=agent_backend,
        llm_provider=llm_provider,
        sandbox_provider=sandbox_provider,
        secrets_store=secrets_store,
        episodic_store=episodic_store,
    )
