"""kopicode as an :class:`~cuttlefish.agents.backend.AgentBackend` (ADR-0003, ADR-0005).

Everything here already existed in ``cuttlefish.tasks.delegate`` before the
backend became pluggable, moved rather than rewritten:
:meth:`KopicodeBackend.delegate` must behave identically to V1/V2's
``delegate_to_kopicode`` (docs/PLAN.md R1) — same policy file lifecycle, same
sandbox mounts, same credential forwarding.
"""

from __future__ import annotations

import asyncio
import functools
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar, Literal

from cuttlefish.agents.outcome import DelegationError, DelegationOutcome
from cuttlefish.delegate.consent import ConsentPolicy, ConsentPolicyError
from cuttlefish.delegate.kopicode import run_kopicode, run_kopicode_in_sandbox
from cuttlefish.delegate.kopicode_serve import (
    UNCONFIGURABLE_WINDOW,
    AskHandler,
    Decider,
    ServePool,
    run_kopicode_serve,
    serve_supports_ask,
    serve_supports_consent_timeout,
    serve_supports_limits,
    serve_supports_usage,
)
from cuttlefish.delegate.policy import write_policy_file
from cuttlefish.limits import (
    context_limit_for,
    max_turns_for,
    round_timeout_for,
    session_token_budget_for,
)
from cuttlefish.requests import AskingDecider, ShellAsker
from cuttlefish.sandbox.provider import (
    SandboxHandle,
    SandboxProvider,
    SandboxSpec,
    StreamingSandboxProvider,
)

#: Where the kopicode binary and its policy file land inside a sandbox, fixed
#: rather than mirroring their host paths (cuttlefish.tasks.delegate's
#: original reasoning, unchanged by the move).
_SANDBOX_KOPICODE_BINARY = "/usr/local/bin/kopicode"
_SANDBOX_POLICY_FILE = "/tmp/cuttlefish-policy.toml"  # inside the sandbox, not the host

#: kopicode's own model-provider credential (docs/QUESTIONS.md Q11).
_CREDENTIAL_ENV_VARS = ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY")


#: Shared by every :class:`KopicodeBackend` in the process: ``delegate_to_agent_backend``
#: resolves a fresh backend per delegation, so a per-instance pool would never be reused.
#: Safe to share -- a child is keyed by binary and credential set.
_SHARED_POOL = ServePool()


async def close_shared_pool() -> None:
    """End every resident ``kopicode serve`` child. Call when the event loop is finishing."""
    await _SHARED_POOL.aclose()


def _credential_envs(secrets: Mapping[str, str]) -> dict[str, str]:
    """Every env var this delegation should carry a credential value for.

    ADR-0006: a name in `secrets` (already resolved from
    :class:`~cuttlefish.secrets.store.SecretsStore`, project scope then shared)
    wins over `os.environ` for the same name -- a project can override the
    ambient credential, not just add to it. A `_CREDENTIAL_ENV_VARS` name absent
    from `secrets` falls back to `os.environ`, exactly today's V1/V2 behaviour
    when no secrets store is configured at all. Any other name in `secrets` (an
    operator-declared secret with no ambient env-var counterpart, e.g. a
    HuggingFace token) is forwarded as-is.
    """
    resolved = {
        name: value
        for name in _CREDENTIAL_ENV_VARS
        if (value := secrets.get(name) or os.environ.get(name))
    }
    resolved.update({name: value for name, value in secrets.items() if name not in resolved})
    return resolved


class KopicodeBackend:
    """Wraps kopicode behind the pluggable backend seam.

    ``transport="serve"`` (the default) drives ``kopicode serve`` and answers its live
    ``consent.request`` from the role's ``allow`` list (``cuttlefish.delegate.consent``).
    With no sandbox it uses a resident child. In a sandbox whose provider can stream
    (:class:`~cuttlefish.sandbox.provider.StreamingSandboxProvider`) it starts one ``serve``
    child inside a fresh sandbox per delegation, under the same policy; destroying the
    sandbox is the cleanup that does not depend on the process cooperating (KAN-1793,
    ADR-0021). A sandbox provider that cannot stream falls back to ``run --print`` inside
    the sandbox with the declared-allowlist policy file, since ``exec`` returns only after
    the process exits. ``transport="print"`` forces ``run --print`` everywhere.
    """

    NAME: ClassVar[str] = "kopicode"
    CREDENTIAL_ENV_VARS: ClassVar[tuple[str, ...]] = _CREDENTIAL_ENV_VARS

    def __init__(
        self,
        binary: str = "kopicode",
        *,
        transport: Literal["serve", "print"] = "serve",
        pool: ServePool | None = None,
    ) -> None:
        self._binary = binary
        self._transport = transport
        self._pool = pool if pool is not None else _SHARED_POOL

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
        """``limits`` are the project's and role's own settings (turns, token budget, context,
        time); a key it lacks falls back to the environment, then the built-in default.

        ``asker`` (ADR-0028) lets a command nothing approves be put to a person, who has
        ``asker.window_s`` to answer (less when this kopicode cannot wait that long). Only the
        ``serve`` transport can hold a request open; ``run --print`` ignores it."""
        if self._transport == "serve" and sandbox_provider is None:
            policy, consent_timeout = await self._consent(allow, mode, asker)
            return await run_kopicode_serve(
                binary=self._binary,
                task_text=task_text,
                root=root,
                policy=policy,
                env=_credential_envs(secrets),
                pool=self._pool,
                consent_timeout=consent_timeout,
                ask=await self._ask_handler(asker, consent_timeout),
                session_limits=await self._session_limits(limits),
                context_limit=await self._context_limit(limits),
                timeout=round_timeout_for(limits),
            )
        if self._transport == "serve" and isinstance(sandbox_provider, StreamingSandboxProvider):
            policy, consent_timeout = await self._consent(allow, mode, asker)
            return await self._delegate_serve_inside_sandbox(
                sandbox_provider,
                task_text=task_text,
                root=root,
                policy=policy,
                secrets=secrets,
                consent_timeout=consent_timeout,
                ask=await self._ask_handler(asker, consent_timeout),
                session_limits=await self._session_limits(limits),
                context_limit=await self._context_limit(limits),
                timeout=round_timeout_for(limits),
            )
        if mode == "auto":
            raise DelegationError(
                "auto mode needs the kopicode serve transport: an exact-match policy file "
                "cannot express it"
            )
        fd, policy_path_str = tempfile.mkstemp(prefix="cuttlefish-policy-", suffix=".toml")
        os.close(fd)
        policy_path = Path(policy_path_str)
        try:
            write_policy_file(policy_path, root=root, allow=allow)
            if sandbox_provider is None:
                return await run_kopicode(
                    binary=self._binary,
                    task_text=task_text,
                    root=root,
                    policy_file=str(policy_path),
                    env=_credential_envs(secrets),
                )
            return await self._delegate_inside_sandbox(
                sandbox_provider,
                task_text=task_text,
                root=root,
                policy_path=policy_path,
                secrets=secrets,
            )
        finally:
            policy_path.unlink(missing_ok=True)

    async def _context_limit(self, limits: Mapping[str, int] | None = None) -> float | None:
        """How much of the model's window a round may fill, when this kopicode reports it (v0.4.0);
        an older one has nothing to read it from (ADR-0030)."""
        return context_limit_for(limits) if await serve_supports_usage(self._binary) else None

    async def _session_limits(self, limits: Mapping[str, int] | None = None) -> dict[str, int]:
        """The turn cap and token budget for a session, when this kopicode takes them (v0.4.0).
        An older one keeps its own defaults (ADR-0030)."""
        if not await serve_supports_limits(self._binary):
            return {}
        return {
            "max_turns": max_turns_for(limits),
            "token_budget": session_token_budget_for(limits),
        }

    async def _ask_handler(
        self, asker: ShellAsker | None, consent_timeout: float | None
    ) -> AskHandler | None:
        """A live answer for the model's own questions, when a person can be asked and this
        kopicode has the wire (``ask.request``, v0.4.0). The wait is the request window, and
        needs the same ``--consent-timeout`` a permission request does."""
        if asker is None or consent_timeout is None or not await serve_supports_ask(self._binary):
            return None
        window = asker.window_s

        async def ask(question: str, context: str) -> str | None:
            return await asker.ask_person(question, context, window_s=window)

        return ask

    async def _consent(
        self, allow: Sequence[Sequence[str]] | None, mode: str, asker: ShellAsker | None
    ) -> tuple[ConsentPolicy | Decider, float | None]:
        """Who answers kopicode's consent requests, and the ``--consent-timeout`` it needs."""
        try:
            policy = ConsentPolicy(allow, auto=mode == "auto")
        except ConsentPolicyError as exc:
            raise DelegationError(f"unusable shell allowlist: {exc}") from exc
        if asker is None or mode in ("auto", "read-only"):
            return policy, None
        if await serve_supports_consent_timeout(self._binary):
            window, timeout = asker.window_s, asker.window_s + 30.0
        else:
            window, timeout = min(asker.window_s, UNCONFIGURABLE_WINDOW), None
        return AskingDecider(allow, asker, window_s=window), timeout

    async def _delegate_serve_inside_sandbox(
        self,
        provider: SandboxProvider,
        *,
        task_text: str,
        root: str,
        policy: ConsentPolicy | Decider,
        secrets: Mapping[str, str],
        consent_timeout: float | None = None,
        ask: AskHandler | None = None,
        session_limits: Mapping[str, int] | None = None,
        context_limit: float | None = None,
        timeout: float | None = None,
    ) -> DelegationOutcome:
        """One sandbox, one ``serve`` child, one session; the sandbox is destroyed after.

        ``provider`` is known to be a :class:`StreamingSandboxProvider` (checked by the
        caller). No policy file is mounted: consent is answered live."""
        resolved_binary = shutil.which(self._binary)
        if resolved_binary is None:
            raise DelegationError(f"kopicode binary {self._binary!r} not found")
        assert isinstance(provider, StreamingSandboxProvider)
        env = _credential_envs(secrets)
        handle = await provider.create(
            SandboxSpec(
                envs=env,
                mounts={resolved_binary: _SANDBOX_KOPICODE_BINARY, root: root},
            )
        )
        try:
            return await run_kopicode_serve(
                binary=_SANDBOX_KOPICODE_BINARY,
                task_text=task_text,
                root=root,
                policy=policy,
                env=env,
                process_factory=functools.partial(
                    self._spawn_serve, provider, handle, cwd=root, consent_timeout=consent_timeout
                ),
                ask=ask,
                session_limits=session_limits,
                context_limit=context_limit,
                timeout=timeout,
            )
        finally:
            await asyncio.shield(provider.destroy(handle))

    @staticmethod
    async def _spawn_serve(
        provider: StreamingSandboxProvider,
        handle: SandboxHandle,
        *,
        cwd: str,
        consent_timeout: float | None = None,
    ) -> asyncio.subprocess.Process:
        flags = [] if consent_timeout is None else ["--consent-timeout", f"{int(consent_timeout)}s"]
        return await provider.spawn(handle, [_SANDBOX_KOPICODE_BINARY, "serve", *flags], cwd=cwd)

    async def _delegate_inside_sandbox(
        self,
        provider: SandboxProvider,
        *,
        task_text: str,
        root: str,
        policy_path: Path,
        secrets: Mapping[str, str],
    ) -> DelegationOutcome:
        resolved_binary = shutil.which(self._binary)
        if resolved_binary is None:
            raise DelegationError(f"kopicode binary {self._binary!r} not found")

        handle = await provider.create(
            SandboxSpec(
                envs=_credential_envs(secrets),
                mounts={
                    resolved_binary: _SANDBOX_KOPICODE_BINARY,
                    root: root,
                    str(policy_path): _SANDBOX_POLICY_FILE,
                },
            )
        )
        try:
            return await run_kopicode_in_sandbox(
                provider,
                handle,
                binary=_SANDBOX_KOPICODE_BINARY,
                task_text=task_text,
                root=root,
                policy_file=_SANDBOX_POLICY_FILE,
            )
        finally:
            await provider.destroy(handle)
