"""Unit: each backend's `_credential_envs` precedence (ADR-0006) -- a
resolved secret wins over `os.environ`, an unresolved credential name falls
back to it, and any other declared secret is forwarded verbatim.
"""

from __future__ import annotations

import pytest

from cuttlefish.agents.claude_code import _credential_envs as claude_code_credential_envs
from cuttlefish.agents.codex import _credential_envs as codex_credential_envs
from cuttlefish.agents.kopicode import _credential_envs as kopicode_credential_envs


def test_kopicode_falls_back_to_os_environ_when_the_store_has_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert kopicode_credential_envs({}) == {"OPENROUTER_API_KEY": "from-env"}


def test_kopicode_prefers_a_resolved_secret_over_the_ambient_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")

    result = kopicode_credential_envs({"ANTHROPIC_API_KEY": "from-project-secret"})

    assert result["ANTHROPIC_API_KEY"] == "from-project-secret"


def test_kopicode_forwards_a_declared_secret_with_no_ambient_env_counterpart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = kopicode_credential_envs({"HUGGINGFACE_TOKEN": "hf_value"})

    assert result == {"HUGGINGFACE_TOKEN": "hf_value"}


def test_kopicode_omits_a_credential_name_absent_from_both_store_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert kopicode_credential_envs({}) == {}


def test_claude_code_only_ever_resolves_its_own_shorter_credential_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "irrelevant-to-claude-code")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert claude_code_credential_envs({}) == {}


def test_claude_code_prefers_a_resolved_secret_over_the_ambient_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")

    result = claude_code_credential_envs({"ANTHROPIC_API_KEY": "from-project-secret"})

    assert result == {"ANTHROPIC_API_KEY": "from-project-secret"}


def test_codex_only_ever_resolves_its_own_openai_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "irrelevant-to-codex")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert codex_credential_envs({}) == {}


def test_codex_prefers_a_resolved_secret_over_the_ambient_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")

    result = codex_credential_envs({"OPENAI_API_KEY": "from-project-secret"})

    assert result == {"OPENAI_API_KEY": "from-project-secret"}
