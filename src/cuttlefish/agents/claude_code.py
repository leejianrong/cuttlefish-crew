"""Headless Claude Code as a second :class:`~cuttlefish.agents.backend.AgentBackend` (ADR-0005).

Proves the pluggable interface generalises past kopicode's own shape.
``cuttlefish.delegate.claude_code``'s own module docstring covers the CLI
mechanics and the honest limits of the policy mapping; this class is the
sandbox-orchestration counterpart to
:class:`~cuttlefish.agents.kopicode.KopicodeBackend`.

**Named credential gap**: this project forwards ``ANTHROPIC_API_KEY`` into a
sandbox, the same mechanism kopicode's own credential uses. That only helps
an operator whose Claude Code is authenticated via an API key. An operator
logged in through Claude Code's own OAuth flow (verified live, 2026-09-20:
this build's own ``claude`` binary authenticates this way, with no
``ANTHROPIC_API_KEY`` set at all) has no session to forward into a fresh
sandbox — a sandboxed :class:`ClaudeCodeBackend` delegation fails closed in
that case. This is a real, accepted gap for this slice (docs/PLAN.md's Open
risks), not a silent one; forwarding an OAuth session into a sandbox is not
attempted here.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping, Sequence
from typing import ClassVar, Literal

from cuttlefish.agents.deciders import command_decider
from cuttlefish.agents.outcome import DelegationError, DelegationOutcome
from cuttlefish.delegate.claude_code import run_claude_code, run_claude_code_in_sandbox
from cuttlefish.delegate.claude_code_live import QuestionHandler, run_claude_code_live
from cuttlefish.limits import round_timeout_for
from cuttlefish.requests import ShellAsker
from cuttlefish.sandbox.provider import SandboxProvider, SandboxSpec

_SANDBOX_CLAUDE_CODE_BINARY = "/usr/local/bin/claude"

#: ``CUTTLEFISH_CLAUDE_CODE_TRANSPORT=print`` runs ``claude -p`` as before (V4-K).
TRANSPORT_ENV = "CUTTLEFISH_CLAUDE_CODE_TRANSPORT"

_CREDENTIAL_ENV_VARS = ("ANTHROPIC_API_KEY",)


def _credential_envs(secrets: Mapping[str, str]) -> dict[str, str]:
    """See `cuttlefish.agents.kopicode._credential_envs` (ADR-0006) -- identical
    precedence (store wins over `os.environ`, any other declared secret is
    forwarded as-is), just this backend's own, shorter credential list."""
    resolved = {
        name: value
        for name in _CREDENTIAL_ENV_VARS
        if (value := secrets.get(name) or os.environ.get(name))
    }
    resolved.update({name: value for name, value in secrets.items() if name not in resolved})
    return resolved


def _question_handler(asker: ShellAsker | None, mode: str) -> QuestionHandler | None:
    """A live answer for the model's own questions, when a person can be asked."""
    if asker is None or mode in ("auto", "read-only"):
        return None
    window = asker.window_s

    async def ask(question: str, context: str, options: Sequence[str]) -> str | None:
        return await asker.ask_person(question, context, window_s=window, options=options)

    return ask


class ClaudeCodeBackend:
    """Wraps headless Claude Code behind the pluggable backend seam.

    ``transport="stdio"`` (the default, V4-K) keeps one ``claude`` process open per round and
    answers its permission requests from the role's ``allow`` list, asking a person when one can
    be asked (``cuttlefish.delegate.claude_code_live``). ``transport="print"`` is the one-shot
    ``claude -p`` with ``--allowedTools`` patterns; it is also what runs inside a sandbox, which
    cannot hold a request open.
    """

    NAME: ClassVar[str] = "claude-code"
    CREDENTIAL_ENV_VARS: ClassVar[tuple[str, ...]] = _CREDENTIAL_ENV_VARS

    def __init__(
        self, binary: str = "claude", *, transport: Literal["stdio", "print"] | None = None
    ) -> None:
        self._binary = binary
        self._transport = transport or (
            "print" if os.environ.get(TRANSPORT_ENV, "").strip().lower() == "print" else "stdio"
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
        """``asker`` (ADR-0028) lets a command nothing approves, and the model's own questions, be
        put to a person; only the ``stdio`` transport can hold a request open. ``limits`` carries
        the round's time limit; the other limits have no control on Claude Code."""
        if sandbox_provider is None and self._transport == "stdio":
            return await run_claude_code_live(
                binary=self._binary,
                task_text=task_text,
                root=root,
                decide=command_decider(allow, mode, asker),
                mode=mode,
                ask=_question_handler(asker, mode),
                env=_credential_envs(secrets),
                timeout=round_timeout_for(limits),
            )
        if sandbox_provider is None:
            return await run_claude_code(
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
            raise DelegationError(f"Claude Code binary {self._binary!r} not found")

        handle = await provider.create(
            SandboxSpec(
                envs=_credential_envs(secrets),
                mounts={resolved_binary: _SANDBOX_CLAUDE_CODE_BINARY, root: root},
            )
        )
        try:
            return await run_claude_code_in_sandbox(
                provider,
                handle,
                binary=_SANDBOX_CLAUDE_CODE_BINARY,
                task_text=task_text,
                root=root,
                allow=allow,
                mode=mode,
            )
        finally:
            await provider.destroy(handle)
