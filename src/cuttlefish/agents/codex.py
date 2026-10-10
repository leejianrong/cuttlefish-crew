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
from collections.abc import Mapping, Sequence
from typing import ClassVar, Literal

from cuttlefish.agents.outcome import DelegationError, DelegationOutcome
from cuttlefish.delegate.codex import (
    ENV_PASSTHROUGH,
    codex_model_settings,
    run_codex,
    run_codex_in_sandbox,
)
from cuttlefish.delegate.codex_app_server import run_codex_app_server
from cuttlefish.delegate.consent import ConsentPolicy, ConsentPolicyError
from cuttlefish.delegate.kopicode_serve import Decider
from cuttlefish.limits import round_timeout_for
from cuttlefish.requests import AskingDecider, ShellAsker
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


def _decider(allow: Sequence[Sequence[str]] | None, mode: str, asker: ShellAsker | None) -> Decider:
    """Who answers Codex's command approvals: the role's policy, and a person for a command no
    rule approves when ``asker`` is given (never in Auto or for a read-only role)."""
    try:
        policy = ConsentPolicy(allow, auto=mode == "auto")
    except ConsentPolicyError as exc:
        raise DelegationError(f"unusable shell allowlist: {exc}") from exc
    if asker is None or mode in ("auto", "read-only"):
        return policy.decide
    return AskingDecider(allow, asker, window_s=asker.window_s)


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
    ) -> DelegationOutcome:
        """``asker`` (ADR-0028) lets a command nothing approves be put to a person; only the
        ``app-server`` transport can hold one open. ``limits`` carries the round's time limit; the
        other limits have no control on Codex."""
        if sandbox_provider is None and self._transport == "app-server":
            model, effort = codex_model_settings()
            return await run_codex_app_server(
                binary=self._binary,
                task_text=task_text,
                root=root,
                decide=_decider(allow, mode, asker),
                sandbox="read-only" if mode == "read-only" else "workspace-write",
                model=model,
                effort=effort,
                env=_credential_envs(secrets),
                env_passthrough=ENV_PASSTHROUGH,
                timeout=round_timeout_for(limits),
            )
        if sandbox_provider is None:
            return await run_codex(
                binary=self._binary,
                task_text=task_text,
                root=root,
                allow=allow,
                mode=mode,
                env=_credential_envs(secrets),
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
