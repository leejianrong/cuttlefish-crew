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
    name: str, *, kopicode_binary: str, claude_code_binary: str, codex_binary: str = "codex"
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
    raise UnknownBackendError(
        f"unknown agent backend {name!r}; expected 'kopicode', 'claude-code', or 'codex'"
    )
