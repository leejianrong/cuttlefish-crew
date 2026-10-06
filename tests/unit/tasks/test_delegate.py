"""Unit: delegate_to_agent_backend resolves the runtime's configured backend
and routes to it (ADR-0005) -- the generic wiring, not any one backend's own
mechanics (see tests/unit/agents/ for those).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish import runtime
from cuttlefish.agents.outcome import DelegationError
from cuttlefish.agents.registry import UnknownBackendError
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.llm.replay import ReplayLlmProvider
from cuttlefish.secrets.store import SecretsStore, generate_key
from cuttlefish.tasks.delegate import delegate_to_agent_backend


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


async def test_the_kopicode_backend_is_used_by_default(tmp_path: Path) -> None:
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode-binary-that-does-not-exist",
        )
    )

    with pytest.raises(DelegationError, match="kopicode binary"):
        await delegate_to_agent_backend("add a .gitignore entry", str(tmp_path))

    store.close()


async def test_the_claude_code_backend_is_used_when_configured(tmp_path: Path) -> None:
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode",
            claude_code_binary="claude-binary-that-does-not-exist",
            agent_backend="claude-code",
        )
    )

    with pytest.raises(DelegationError, match="Claude Code binary"):
        await delegate_to_agent_backend("add a .gitignore entry", str(tmp_path))

    store.close()


async def test_the_codex_backend_is_used_when_configured(tmp_path: Path) -> None:
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode",
            codex_binary="codex-binary-that-does-not-exist",
            agent_backend="codex",
        )
    )

    with pytest.raises(DelegationError, match="Codex binary"):
        await delegate_to_agent_backend("add a .gitignore entry", str(tmp_path))

    store.close()


async def test_secrets_are_resolved_from_the_store_scoped_to_the_declared_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0006: `project`/`secret_names` reach `SecretsStore.resolve`, unioned
    with the backend's own always-relevant credential names."""
    store = EpisodicStore.open(tmp_path / "episodic.db")
    secrets_store = SecretsStore.open(tmp_path / "secrets.db", key=generate_key())
    secrets_store.set("demo-project", "HUGGINGFACE_TOKEN", "hf_value")

    calls: list[tuple[str, list[str]]] = []
    original_resolve = secrets_store.resolve

    def spy(project: str, names: list[str]) -> dict[str, str]:
        calls.append((project, sorted(names)))
        return original_resolve(project, names)

    monkeypatch.setattr(secrets_store, "resolve", spy)

    runtime.configure(
        runtime.Runtime(
            episodic_store=store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode-binary-that-does-not-exist",
            secrets_store=secrets_store,
        )
    )

    with pytest.raises(DelegationError):
        await delegate_to_agent_backend(
            "add a .gitignore entry",
            str(tmp_path),
            project="demo-project",
            secret_names=["HUGGINGFACE_TOKEN"],
        )

    assert calls == [
        ("demo-project", ["ANTHROPIC_API_KEY", "HUGGINGFACE_TOKEN", "OPENROUTER_API_KEY"])
    ]

    store.close()
    secrets_store.close()


async def test_an_unknown_configured_backend_raises_rather_than_silently_falling_back(
    tmp_path: Path,
) -> None:
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode",
            agent_backend="not-a-real-backend",
        )
    )

    with pytest.raises(UnknownBackendError):
        await delegate_to_agent_backend("add a .gitignore entry", str(tmp_path))

    store.close()


class _RecordingBackend:
    CREDENTIAL_ENV_VARS: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.allow: list[list[str]] | None = None

    async def delegate(self, *, allow: list[list[str]] | None, **_: object) -> object:
        self.allow = allow
        return object()


@pytest.mark.parametrize(
    ("access", "expect_write"),
    [(None, True), ("read-only", False)],
)
async def test_access_decides_which_shell_the_backend_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, access: str | None, expect_write: bool
) -> None:
    backend = _RecordingBackend()
    monkeypatch.setattr("cuttlefish.tasks.delegate.resolve_backend", lambda *a, **k: backend)
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store, llm_provider=ReplayLlmProvider([]), kopicode_binary="kopicode"
        )
    )

    kwargs = {"access": access} if access else {}
    await delegate_to_agent_backend("t", str(tmp_path), allow=[["go", "test"]], **kwargs)

    assert backend.allow is not None
    assert (["git", "add"] in backend.allow) is expect_write
    assert (["go", "test"] in backend.allow) is expect_write  # read-only ignores declarations
    assert ["git", "status"] in backend.allow  # everyone can look
    store.close()
