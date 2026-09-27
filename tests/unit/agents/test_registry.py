"""Unit: resolve_backend picks the right AgentBackend implementation, or fails
loudly for an unrecognised name rather than silently falling back (ADR-0005).
"""

from __future__ import annotations

import pytest

from cuttlefish.agents.claude_code import ClaudeCodeBackend
from cuttlefish.agents.codex import CodexBackend
from cuttlefish.agents.kopicode import KopicodeBackend
from cuttlefish.agents.registry import UnknownBackendError, resolve_backend


def test_kopicode_resolves_to_a_kopicode_backend_with_the_configured_binary() -> None:
    backend = resolve_backend(
        "kopicode", kopicode_binary="my-kopicode", claude_code_binary="claude"
    )
    assert isinstance(backend, KopicodeBackend)
    assert backend._binary == "my-kopicode"


def test_claude_code_resolves_to_a_claude_code_backend_with_the_configured_binary() -> None:
    backend = resolve_backend(
        "claude-code", kopicode_binary="kopicode", claude_code_binary="my-claude"
    )
    assert isinstance(backend, ClaudeCodeBackend)
    assert backend._binary == "my-claude"


def test_codex_resolves_to_a_codex_backend_with_the_configured_binary() -> None:
    backend = resolve_backend(
        "codex", kopicode_binary="kopicode", claude_code_binary="claude", codex_binary="my-codex"
    )
    assert isinstance(backend, CodexBackend)
    assert backend._binary == "my-codex"


def test_an_unknown_name_raises_rather_than_falling_back_to_a_default() -> None:
    with pytest.raises(UnknownBackendError):
        resolve_backend(
            "not-a-real-backend", kopicode_binary="kopicode", claude_code_binary="claude"
        )
