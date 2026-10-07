"""Unit: delegate_to_agent_backend resolves the runtime's configured backend
and routes to it (ADR-0005) -- the generic wiring, not any one backend's own
mechanics (see tests/unit/agents/ for those).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish import runtime
from cuttlefish.agents.outcome import DelegationError, DelegationOutcome
from cuttlefish.agents.registry import UnknownBackendError
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.llm.replay import ReplayLlmProvider
from cuttlefish.requests import RequestBroker, RequestContext
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
        self.mode: str | None = None

    async def delegate(
        self, *, allow: list[list[str]] | None, mode: str | None = None, **_: object
    ) -> object:
        self.allow = allow
        self.mode = mode
        return object()


async def _delegate_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, access: str | None
) -> _RecordingBackend:
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
    store.close()
    return backend


async def test_standard_gets_the_presets_plus_declared_and_passes_no_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = await _delegate_with(tmp_path, monkeypatch, None)
    assert backend.allow is not None
    assert ["git", "add"] in backend.allow
    assert ["go", "test"] in backend.allow
    assert backend.mode is None


async def test_read_only_gets_inspection_only_and_ignores_declarations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = await _delegate_with(tmp_path, monkeypatch, "read-only")
    assert backend.allow is not None
    assert ["git", "status"] in backend.allow
    assert ["git", "add"] not in backend.allow
    assert ["go", "test"] not in backend.allow
    assert backend.mode == "read-only"


async def test_ask_first_gets_no_commands_and_passes_no_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = await _delegate_with(tmp_path, monkeypatch, "ask-first")
    assert backend.allow == []
    assert backend.mode is None


async def test_auto_gets_the_presets_so_a_list_driven_backend_still_has_one_and_the_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = await _delegate_with(tmp_path, monkeypatch, "auto")
    assert backend.allow
    assert backend.mode == "auto"


async def test_chosen_presets_reach_the_backend_in_place_of_the_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _RecordingBackend()
    monkeypatch.setattr("cuttlefish.tasks.delegate.resolve_backend", lambda *a, **k: backend)
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store, llm_provider=ReplayLlmProvider([]), kopicode_binary="kopicode"
        )
    )
    await delegate_to_agent_backend(
        "t", str(tmp_path), allow=[["go", "test"]], presets=["inspect", "containers"]
    )
    store.close()
    assert backend.allow is not None
    assert ["ls"] in backend.allow and ["docker", "compose", "up"] in backend.allow
    assert ["go", "test"] in backend.allow
    assert ["uv", "run", "pytest"] not in backend.allow


class _StuckBackend(_RecordingBackend):
    NAME = "kopicode"

    def __init__(self, failure_kind: str | None) -> None:
        super().__init__()
        self._kind = failure_kind

    async def delegate(self, **_: object) -> DelegationOutcome:
        return DelegationOutcome(
            kind="failed",
            summary="s",
            reason="stopped after 5 shell commands in a row failed",
            failure_kind=self._kind,
            detail="No module named 'numpy'",
        )


async def _stuck_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str | None, *, closed: bool = False
) -> RequestBroker:
    backend = _StuckBackend(kind)
    monkeypatch.setattr("cuttlefish.tasks.delegate.resolve_backend", lambda *a, **k: backend)
    store = EpisodicStore.open(tmp_path / "episodic.db")
    broker = RequestBroker(lambda team, payload: store.append(team, payload))
    context = RequestContext(broker, "p1", "t1", 60.0)
    context.note_role("builder", "do it")
    if closed:
        broker.end_team("t1", "cancelled")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store,
            llm_provider=ReplayLlmProvider([]),
            kopicode_binary="kopicode",
            requests=context,
        )
    )
    await delegate_to_agent_backend("do it", str(tmp_path))
    store.close()
    return broker


async def test_an_agent_stuck_on_its_environment_raises_a_blocked_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = await _stuck_round(tmp_path, monkeypatch, "environment_stuck")
    (pending,) = broker.pending("p1")
    assert pending.record.kind == "blocked"
    assert pending.record.role == "builder"
    assert pending.record.detail == "No module named 'numpy'"
    assert pending.record.answers == []


async def test_any_other_failure_raises_no_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await _stuck_round(tmp_path, monkeypatch, "max_turns")).pending() == []


async def test_a_stopped_team_raises_no_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = await _stuck_round(tmp_path, monkeypatch, "environment_stuck", closed=True)
    assert broker.pending() == []
