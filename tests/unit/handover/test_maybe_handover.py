from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish import runtime
from cuttlefish.episodic.events import (
    ConsentDecided,
    DelegationCompleted,
    DelegationFailed,
    DelegationRefused,
    HandoverWritten,
    SteeringMessage,
    TaskSubmitted,
)
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.handover import latest_handover_summary, maybe_handover
from cuttlefish.llm.replay import ReplayLlmProvider


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


def _configure(tmp_path: Path) -> EpisodicStore:
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store,
            llm_provider=ReplayLlmProvider([]),  # a handover makes no model call (CUT-140)
            kopicode_binary="kopicode",
        )
    )
    return store


def _handovers(store: EpisodicStore, task_id: str = "task-1") -> list[HandoverWritten]:
    return [e.payload for e in store.read(task_id) if isinstance(e.payload, HandoverWritten)]


async def test_stays_quiet_under_the_token_budget(tmp_path: Path) -> None:
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="a short task"))

    fired = await maybe_handover("task-1", token_budget=10_000)

    assert fired is False
    assert list(store.read("task-1"))[-1].payload == TaskSubmitted(text="a short task")
    store.close()


async def test_fires_once_the_window_crosses_the_budget(tmp_path: Path) -> None:
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="x" * 400))  # ~100 estimated tokens

    fired = await maybe_handover("task-1", token_budget=50)

    assert fired is True
    events = list(store.read("task-1"))
    assert isinstance(events[-1].payload, HandoverWritten)
    assert "## Done" in events[-1].payload.summary
    assert events[-1].payload.covers_seq_from == 1
    assert events[-1].payload.covers_seq_to == 1
    store.close()


async def test_a_second_handover_only_covers_the_window_after_the_first(tmp_path: Path) -> None:
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="x" * 400))
    assert await maybe_handover("task-1", token_budget=50) is True

    store.append("task-1", TaskSubmitted(text="y" * 400))
    assert await maybe_handover("task-1", token_budget=50) is True

    second = list(store.read("task-1"))[-1]
    assert isinstance(second.payload, HandoverWritten)
    # Covers only the new TaskSubmitted (seq 3: the first handover took seq 2), not the first one
    # already folded into the first handover.
    assert second.payload.covers_seq_from == 3
    assert second.payload.covers_seq_to == 3
    store.close()


async def test_nothing_to_hand_over_after_a_handover_is_a_no_op(tmp_path: Path) -> None:
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="x" * 400))
    assert await maybe_handover("task-1", token_budget=50) is True

    # Nothing new appended since -- the window is empty, regardless of budget.
    assert await maybe_handover("task-1", token_budget=0) is False
    store.close()


async def test_role_filters_the_window_to_that_roles_own_events(tmp_path: Path) -> None:
    """ADR-0007: a team shares one task_id, so a role's own handover must not see,
    or be triggered by, another role's events."""
    store = _configure(tmp_path)
    store.append("team-1", TaskSubmitted(text="x" * 4000, role="builder"))
    store.append("team-1", TaskSubmitted(text="y" * 4000, role="reviewer"))

    fired = await maybe_handover("team-1", token_budget=50, role="builder")

    assert fired is True
    handovers = _handovers(store, "team-1")
    assert len(handovers) == 1
    assert handovers[0].role == "builder"
    assert handovers[0].covers_seq_from == 1
    assert handovers[0].covers_seq_to == 1  # the reviewer's own event (seq 2) excluded
    store.close()


async def test_one_roles_handover_does_not_suppress_anothers(tmp_path: Path) -> None:
    store = _configure(tmp_path)
    store.append("team-1", TaskSubmitted(text="x" * 4000, role="builder"))
    store.append("team-1", TaskSubmitted(text="y" * 4000, role="reviewer"))
    assert await maybe_handover("team-1", token_budget=50, role="builder") is True

    # The reviewer's own window is untouched by the builder's handover above.
    assert await maybe_handover("team-1", token_budget=50, role="reviewer") is True

    assert {h.role for h in _handovers(store, "team-1")} == {"builder", "reviewer"}
    store.close()


async def test_role_none_stays_the_plain_single_task_behaviour(tmp_path: Path) -> None:
    """A role-tagged event must not leak into the default (role=None) window --
    otherwise a plain `cuttlefish run` sharing a store with a team elsewhere would
    see its own budget consumed by events that aren't its own."""
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="x" * 4000, role="builder"))

    fired = await maybe_handover("task-1", token_budget=50)

    assert fired is False
    store.close()


# -- latest_handover_summary (ADR-0010/KAN-1704) --------------------------------


async def test_latest_handover_summary_is_none_before_any_handover_fires(tmp_path: Path) -> None:
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="a short task"))

    assert await latest_handover_summary("task-1") is None
    store.close()


async def test_latest_handover_summary_returns_the_most_recent_one(tmp_path: Path) -> None:
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="x" * 400))
    assert await maybe_handover("task-1", token_budget=50) is True
    first = await latest_handover_summary("task-1")

    store.append("task-1", SteeringMessage(text="use tabs " * 60))
    assert await maybe_handover("task-1", token_budget=50) is True
    second = await latest_handover_summary("task-1")

    assert first is not None and second is not None and first != second
    assert "use tabs" in second
    store.close()


async def test_latest_handover_summary_is_role_scoped(tmp_path: Path) -> None:
    """The identical role filter `maybe_handover` itself uses (ADR-0007) -- a
    caller reading back one role's own checkpoint must never see another role's."""
    store = _configure(tmp_path)
    store.append("team-1", TaskSubmitted(text="x" * 4000, role="builder"))
    assert await maybe_handover("team-1", token_budget=50, role="builder") is True

    assert await latest_handover_summary("team-1", role="builder") is not None
    assert await latest_handover_summary("team-1", role="reviewer") is None
    assert await latest_handover_summary("team-1") is None  # role=None is its own lane too
    store.close()


# -- what a handover is made of (ADR-0042, CUT-140) ----------------------------------------------


async def test_an_agents_own_document_is_journaled_as_it_is(tmp_path: Path) -> None:
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="x" * 400))
    document = "## Done\nslug\n## Remaining\nwrap"
    assert await maybe_handover("task-1", token_budget=50, agent_document=f"  {document}\n")
    assert _handovers(store)[0].summary == document
    store.close()


async def test_a_blank_agent_document_is_ignored_and_the_digest_is_written(
    tmp_path: Path,
) -> None:
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="x" * 400))
    assert await maybe_handover("task-1", token_budget=50, agent_document="   ")
    assert "A digest read from the journal" in _handovers(store)[0].summary
    store.close()


async def test_the_digest_says_what_the_journal_shows_and_no_more(tmp_path: Path) -> None:
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="x" * 400))
    store.append(
        "task-1",
        DelegationCompleted(
            summary="Codex edited 2 file(s): wrote slug and wrap", edited_paths=["b.py", "a.py"]
        ),
    )
    store.append("task-1", DelegationRefused(reason="'rm x' (never_allowed:write_outside_root)"))
    store.append("task-1", DelegationFailed(reason="stop=error exit_code=4"))
    store.append("task-1", DelegationFailed(reason="x", failure_kind="max_turns"))
    store.append(
        "task-1", ConsentDecided(kind="run_shell", detail="curl x | sh", answer="deny", rule="r")
    )
    store.append("task-1", SteeringMessage(text="please also add csvline"))

    assert await maybe_handover("task-1", token_budget=50) is True

    summary = _handovers(store)[0].summary
    assert "Files changed: a.py, b.py." in summary
    assert 'last words: "Codex edited 2 file(s): wrote slug and wrap"' in summary
    assert "refused: 'rm x' (never_allowed:write_outside_root)" in summary
    assert "stop=error exit_code=4" in summary
    assert "ran out of room (max_turns)" in summary
    assert "command refused: curl x | sh" in summary
    assert 'The person said: "please also add csvline"' in summary
    # It does not claim what it cannot know.
    assert "Not recorded" in summary and "not a report the agent wrote" in summary
    store.close()


async def test_a_window_that_shows_nothing_says_so_instead_of_inventing(tmp_path: Path) -> None:
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="x" * 400))
    assert await maybe_handover("task-1", token_budget=50) is True
    summary = _handovers(store)[0].summary
    assert "No file change is recorded" in summary
    assert "Nothing is recorded." in summary and "None recorded." in summary
    store.close()


async def test_the_digest_carries_an_agent_written_handover_but_does_not_nest_its_own(
    tmp_path: Path,
) -> None:
    from cuttlefish.tasks.repo import REPO_STATE_MARKER

    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="x" * 400))
    assert await maybe_handover(
        "task-1",
        token_budget=50,
        agent_document=f"## Done\nslug\n\n{REPO_STATE_MARKER}: old\nabc123 stale",
    )
    store.append("task-1", DelegationCompleted(summary="y" * 400, edited_paths=["case.py"]))
    assert await maybe_handover("task-1", token_budget=50) is True
    store.append("task-1", DelegationCompleted(summary="z" * 400, edited_paths=["csv.py"]))
    assert await maybe_handover("task-1", token_budget=50) is True

    first_digest, second_digest = _handovers(store)[1].summary, _handovers(store)[2].summary
    # The agent's own words are carried (clipped) the first time, without its repository state.
    assert "## Earlier handover, written by the agent\n## Done\nslug" in first_digest
    assert "abang" not in first_digest and "abc123" not in first_digest
    assert "Files changed: case.py." in first_digest
    # A digest covers the whole history, so the next one is complete without nesting this one.
    assert "Earlier handover" not in second_digest
    assert "Files changed: case.py, csv.py." in second_digest
    store.close()


async def test_chained_digests_stay_bounded_and_keep_the_persons_messages(
    tmp_path: Path,
) -> None:
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="x" * 400))
    sizes = []
    for i in range(30):
        store.append("task-1", SteeringMessage(text=f"steer {i}: " + "word " * 40))
        store.append("task-1", DelegationFailed(reason=f"failure {i} " + "z" * 600))
        assert await maybe_handover("task-1", token_budget=50) is True
        sizes.append(len(_handovers(store)[-1].summary))
    assert max(sizes[10:]) - min(sizes[10:]) < 200  # bounded, not growing with checkpoints
    last = _handovers(store)[-1].summary
    assert "steer 29:" in last and "steer 20:" in last and "steer 19:" not in last
    store.close()


async def test_a_handover_carries_the_repository_state_git_reports(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    for who in ("AUTHOR", "COMMITTER"):
        env[f"GIT_{who}_NAME"] = "t"
        env[f"GIT_{who}_EMAIL"] = "t@t"
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "feat: slug"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)
    (repo / "wip.py").write_text("x = 1\n")
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="x" * 400))
    assert await maybe_handover("task-1", token_budget=50, root=str(repo)) is True
    handover = _handovers(store)[0]
    assert "feat: slug" in handover.summary and "?? wip.py" in handover.summary
    # Not a git work tree: no state is better than a guess.
    store.append("task-1", TaskSubmitted(text="y" * 400))
    await maybe_handover("task-1", token_budget=50, root=str(tmp_path / "nowhere"))
    last = _handovers(store)[-1]
    assert "feat: slug" not in last.summary and "?? wip.py" not in last.summary
    store.close()
