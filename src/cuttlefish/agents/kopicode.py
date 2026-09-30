"""kopicode as an :class:`~cuttlefish.agents.backend.AgentBackend` (ADR-0003, ADR-0005).

Everything here already existed in ``cuttlefish.tasks.delegate`` before the
backend became pluggable, moved rather than rewritten:
:meth:`KopicodeBackend.delegate` must behave identically to V1/V2's
``delegate_to_kopicode`` (docs/PLAN.md R1) — same policy file lifecycle, same
sandbox mounts, same credential forwarding.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar, Literal

from cuttlefish.agents.outcome import DelegationError, DelegationOutcome
from cuttlefish.delegate.consent import ConsentPolicy, ConsentPolicyError
from cuttlefish.delegate.kopicode import run_kopicode, run_kopicode_in_sandbox
from cuttlefish.delegate.kopicode_serve import ServePool, run_kopicode_serve
from cuttlefish.delegate.policy import write_policy_file
from cuttlefish.sandbox.provider import SandboxProvider, SandboxSpec

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
    ``consent.request`` from the role's ``allow`` list (``cuttlefish.delegate.consent``),
    for a delegation with no sandbox provider. A sandboxed delegation always uses
    ``run --print`` inside the sandbox, with the declared-allowlist policy file: a
    resident stdio child is not something ``SandboxProvider.exec`` can host (it returns
    only after the process exits), and inside a sandbox the sandbox is the containment.
    ``transport="print"`` forces ``run --print`` everywhere.
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
    ) -> DelegationOutcome:
        if self._transport == "serve" and sandbox_provider is None:
            try:
                policy = ConsentPolicy(allow)
            except ConsentPolicyError as exc:
                raise DelegationError(f"unusable shell allowlist: {exc}") from exc
            return await run_kopicode_serve(
                binary=self._binary,
                task_text=task_text,
                root=root,
                policy=policy,
                env=_credential_envs(secrets),
                pool=self._pool,
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
