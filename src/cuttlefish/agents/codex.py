"""Headless Codex CLI as a third :class:`~cuttlefish.agents.backend.AgentBackend`
(ADR-0005, KAN-1713, ADR-0018).

Proves the pluggable interface generalises a second time past kopicode's own
shape, the same way :class:`~cuttlefish.agents.claude_code.ClaudeCodeBackend`
proved it the first time. ``cuttlefish.delegate.codex``'s own module
docstring covers the CLI mechanics and every honestly-named gap (the coarse
sandbox-tier policy mapping, the stderr-heuristic refusal detection, the
inert `OPENAI_API_KEY` forwarding) -- this class is the sandbox-orchestration
counterpart to :class:`~cuttlefish.agents.kopicode.KopicodeBackend`.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from typing import ClassVar, Literal

from cuttlefish.agents.deciders import command_decider
from cuttlefish.agents.outcome import DelegationError, DelegationOutcome
from cuttlefish.broker import BrokerRoute, Lease
from cuttlefish.delegate.codex import (
    BROKER_TOKEN_ENV,
    ENV_PASSTHROUGH,
    codex_model_settings,
    run_codex,
    run_codex_in_sandbox,
)
from cuttlefish.delegate.codex_app_server import run_codex_app_server
from cuttlefish.limits import round_timeout_for
from cuttlefish.requests import ShellAsker
from cuttlefish.sandbox.provider import SandboxProvider, SandboxSpec

#: ``CUTTLEFISH_CODEX_TRANSPORT=exec`` runs ``codex exec`` as before (V4-M).
TRANSPORT_ENV = "CUTTLEFISH_CODEX_TRANSPORT"

_SANDBOX_CODEX_BINARY = "/usr/local/bin/codex"

#: Codex's own ambient credential name (`codex login --with-api-key` reads it
#: from stdin, `printenv OPENAI_API_KEY | codex login --with-api-key`) --
#: forwarded the same way every other backend's ambient credential is,
#: even though it's verified inert for `codex exec` itself (see
#: `cuttlefish.delegate.codex`'s own module doc comment).
_CREDENTIAL_ENV_VARS = ("OPENAI_API_KEY",)


def _credential_envs(secrets: Mapping[str, str]) -> dict[str, str]:
    """See `cuttlefish.agents.kopicode._credential_envs` (ADR-0006) -- identical
    precedence, this backend's own (currently inert, named honestly) credential
    list."""
    resolved = {
        name: value
        for name in _CREDENTIAL_ENV_VARS
        if (value := secrets.get(name) or os.environ.get(name))
    }
    resolved.update({name: value for name, value in secrets.items() if name not in resolved})
    return resolved


def _env(secrets: Mapping[str, str], lease: Lease | None) -> dict[str, str]:
    """What Codex's process is given: its declared credentials and, when a lease is held
    (ADR-0031), the broker's token for the provider that points at it."""
    env = _credential_envs(secrets)
    if lease is not None:
        env.pop("OPENAI_API_KEY", None)
        env[BROKER_TOKEN_ENV] = lease.token
    return env


class CodexBackend:
    """Wraps headless Codex behind the pluggable backend seam.

    ``transport="app-server"`` (the default, V4-M) drives ``codex app-server`` and answers every
    command's approval from the role's ``allow`` list, asking a person when one can be asked
    (``cuttlefish.delegate.codex_app_server``). ``transport="exec"`` is the one-shot
    ``codex exec`` with its coarse sandbox tier; it is also what runs inside a sandbox, since
    ``app-server`` needs a process that streams.
    """

    NAME: ClassVar[str] = "codex"
    CREDENTIAL_ENV_VARS: ClassVar[tuple[str, ...]] = _CREDENTIAL_ENV_VARS
    #: Its key can be held by the credential broker (ADR-0031), but only one set in Secrets: under a
    #: ChatGPT login Codex ignores an `OPENAI_API_KEY` in the environment.
    BROKER_ROUTE: ClassVar[BrokerRoute] = BrokerRoute(
        "openai", "OPENAI_API_KEY", "OPENAI_BASE_URL", ambient=False
    )

    def __init__(
        self, binary: str = "codex", *, transport: Literal["app-server", "exec"] | None = None
    ) -> None:
        self._binary = binary
        self._transport = transport or (
            "exec" if os.environ.get(TRANSPORT_ENV, "").strip().lower() == "exec" else "app-server"
        )

    async def delegate(
        self,
        *,
        task_text: str,
        root: str,
        allow: list[list[str]] | None,
        secrets: Mapping[str, str],
        sandbox_provider: SandboxProvider | None,
        mode: str = "standard",
        asker: ShellAsker | None = None,
        limits: Mapping[str, int] | None = None,
        lease: Lease | None = None,
    ) -> DelegationOutcome:
        """``lease`` (ADR-0031) is the broker's stand-in for the key, taken by a delegation that
        runs on the host; a sandbox cannot reach the daemon's loopback and is given none.
        ``asker`` (ADR-0028) lets a command nothing approves be put to a person; only the
        ``app-server`` transport can hold one open. ``limits`` carries the round's time limit; the
        other limits have no control on Codex."""
        if sandbox_provider is None and self._transport == "app-server":
            model, effort = codex_model_settings()
            return await run_codex_app_server(
                binary=self._binary,
                task_text=task_text,
                root=root,
                decide=command_decider(allow, mode, asker),
                sandbox="read-only" if mode == "read-only" else "workspace-write",
                model=model,
                effort=effort,
                env=_env(secrets, lease),
                env_passthrough=ENV_PASSTHROUGH,
                timeout=round_timeout_for(limits),
                lease=lease,
            )
        if sandbox_provider is None:
            return await run_codex(
                binary=self._binary,
                task_text=task_text,
                root=root,
                allow=allow,
                mode=mode,
                env=_env(secrets, lease),
                lease=lease,
            )
        return await self._delegate_inside_sandbox(
            sandbox_provider,
            task_text=task_text,
            root=root,
            allow=allow,
            mode=mode,
            secrets=secrets,
        )

    async def _delegate_inside_sandbox(
        self,
        provider: SandboxProvider,
        *,
        task_text: str,
        root: str,
        allow: list[list[str]] | None,
        mode: str,
        secrets: Mapping[str, str],
    ) -> DelegationOutcome:
        resolved_binary = shutil.which(self._binary)
        if resolved_binary is None:
            raise DelegationError(f"Codex binary {self._binary!r} not found")

        handle = await provider.create(
            SandboxSpec(
                envs=_credential_envs(secrets),
                mounts={resolved_binary: _SANDBOX_CODEX_BINARY, root: root},
            )
        )
        try:
            return await run_codex_in_sandbox(
                provider,
                handle,
                binary=_SANDBOX_CODEX_BINARY,
                task_text=task_text,
                root=root,
                allow=allow,
                mode=mode,
            )
        finally:
            await provider.destroy(handle)
