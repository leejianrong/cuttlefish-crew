"""The pluggable coding-agent backend seam (ADR-0005).

Generalises what was kopicode-only through ADR-0003 into a Protocol: any
:class:`AgentBackend` invokes its own tool, parses its own native output into
one :class:`~cuttlefish.agents.outcome.DelegationOutcome`, and owns whatever
policy/sandbox mechanics its own tool actually supports — honestly, not by
pretending to a uniform guarantee no backend can actually promise. See each
backend's own module (``cuttlefish.agents.kopicode``,
``cuttlefish.agents.claude_code``) for what it can and can't do.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Protocol

from cuttlefish.agents.outcome import DelegationOutcome
from cuttlefish.sandbox.provider import SandboxProvider


class AgentBackend(Protocol):
    """One coding-agent tool cuttlefish-crew can delegate a task to.

    ``NAME`` is this project's own stable discriminator (mirroring
    :class:`~cuttlefish.sandbox.provider.SandboxProvider.BACKEND_NAME`): the
    episodic journal records which backend actually ran a delegation
    (``DelegationStarted.backend``), and that has to come from somewhere
    stable, not a Python class name a refactor could quietly change.

    ``CREDENTIAL_ENV_VARS`` (ADR-0006) names the environment variables this
    backend always tries to resolve for a delegation, whether or not the
    operator declared them via ``cuttlefish run --secret`` — the same two
    names (``OPENROUTER_API_KEY``/``ANTHROPIC_API_KEY`` for kopicode,
    ``ANTHROPIC_API_KEY`` alone for Claude Code) each backend has always
    forwarded ambiently. A caller resolves these plus any explicitly declared
    names from :class:`~cuttlefish.secrets.store.SecretsStore` and passes the
    result as ``secrets``.

    ``mode`` (V4-C, ADR-0025) is ``"standard"``, ``"auto"`` or ``"read-only"``; the caller
    passes it only when it is not ``"standard"``. ``allow`` already carries the rest (an
    empty list is ask-first). Each backend maps ``mode`` onto its own controls.
    """

    NAME: ClassVar[str]
    CREDENTIAL_ENV_VARS: ClassVar[tuple[str, ...]]

    async def delegate(
        self,
        *,
        task_text: str,
        root: str,
        allow: list[list[str]] | None,
        secrets: Mapping[str, str],
        sandbox_provider: SandboxProvider | None,
        mode: str = "standard",
    ) -> DelegationOutcome: ...
