"""Resolves a configured backend name to an :class:`~cuttlefish.agents.backend.AgentBackend`.

ADR-0005 is the decision behind this seam.

Imports the concrete backend classes lazily, inside the function body — the
same convention ``cuttlefish.cli`` already uses for the LLM and sandbox
providers — so importing this module never pulls in both backends' own
dependency chains just to resolve one of them.
"""

from __future__ import annotations

from cuttlefish.agents.backend import AgentBackend


class UnknownBackendError(Exception):
    """A configured agent-backend name that doesn't match any known AgentBackend."""


def resolve_backend(
    name: str,
    *,
    kopicode_binary: str,
    claude_code_binary: str,
    codex_binary: str = "codex",
    acp_command: tuple[str, ...] = (),
) -> AgentBackend:
    if name == "kopicode":
        from cuttlefish.agents.kopicode import KopicodeBackend

        return KopicodeBackend(kopicode_binary)
    if name == "claude-code":
        from cuttlefish.agents.claude_code import ClaudeCodeBackend

        return ClaudeCodeBackend(claude_code_binary)
    if name == "codex":
        from cuttlefish.agents.codex import CodexBackend

        return CodexBackend(codex_binary)
    if name == "acp":
        from cuttlefish.agents.acp import AcpBackend

        if not acp_command:
            raise UnknownBackendError("the acp backend needs CUTTLEFISH_ACP_AGENT")
        return AcpBackend(acp_command[0], acp_command[1:])
    raise UnknownBackendError(
        f"unknown agent backend {name!r}; expected 'kopicode', 'claude-code', 'codex', or 'acp'"
    )


def credential_names() -> frozenset[str]:
    """Every variable a backend runs on (``AgentBackend.CREDENTIAL_ENV_VARS``): the agent
    credentials, as opposed to a project's own secrets."""
    from cuttlefish.agents.claude_code import ClaudeCodeBackend
    from cuttlefish.agents.codex import CodexBackend
    from cuttlefish.agents.kopicode import KopicodeBackend

    return frozenset(
        name
        for backend in (KopicodeBackend, ClaudeCodeBackend, CodexBackend)
        for name in backend.CREDENTIAL_ENV_VARS
    )
