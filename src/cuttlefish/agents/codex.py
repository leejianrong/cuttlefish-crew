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
from typing import ClassVar

from cuttlefish.agents.outcome import DelegationError, DelegationOutcome
from cuttlefish.delegate.codex import run_codex, run_codex_in_sandbox
from cuttlefish.sandbox.provider import SandboxProvider, SandboxSpec

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


class CodexBackend:
    """Wraps headless Codex (``codex exec``) behind the pluggable backend seam."""

    NAME: ClassVar[str] = "codex"
    CREDENTIAL_ENV_VARS: ClassVar[tuple[str, ...]] = _CREDENTIAL_ENV_VARS

    def __init__(self, binary: str = "codex") -> None:
        self._binary = binary

    async def delegate(
        self,
        *,
        task_text: str,
        root: str,
        allow: list[list[str]] | None,
        secrets: Mapping[str, str],
        sandbox_provider: SandboxProvider | None,
        mode: str = "standard",
    ) -> DelegationOutcome:
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
