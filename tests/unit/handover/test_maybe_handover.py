from __future__ import annotations

from pathlib import Path

import pytest

from cuttlefish import runtime
from cuttlefish.episodic.events import (
    DelegationCompleted,
    HandoverWritten,
    LlmCallCompleted,
    TaskSubmitted,
)
from cuttlefish.episodic.store import EpisodicStore
from cuttlefish.handover import latest_handover_summary, maybe_handover
from cuttlefish.llm.provider import LlmResponse
from cuttlefish.llm.replay import ReplayLlmProvider


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    yield
    runtime.reset()


def _configure(tmp_path: Path, *responses: LlmResponse) -> EpisodicStore:
    store = EpisodicStore.open(tmp_path / "episodic.db")
    runtime.configure(
        runtime.Runtime(
            episodic_store=store,
            llm_provider=ReplayLlmProvider(list(responses)),
            kopicode_binary="kopicode",
        )
    )
    return store


async def test_stays_quiet_under_the_token_budget(tmp_path: Path) -> None:
    store = _configure(tmp_path)
    store.append("task-1", TaskSubmitted(text="a short task"))

    fired = await maybe_handover("task-1", token_budget=10_000)

    assert fired is False
    assert list(store.read("task-1"))[-1].payload == TaskSubmitted(text="a short task")
    store.close()


async def test_fires_once_the_window_crosses_the_budget(tmp_path: Path) -> None:
    store = _configure(
        tmp_path, LlmResponse(model="replay", text="Done: a distilled summary of the work so far")
    )
    store.append("task-1", TaskSubmitted(text="x" * 400))  # ~100 estimated tokens

    fired = await maybe_handover("task-1", token_budget=50)

    assert fired is True
    events = list(store.read("task-1"))
    assert isinstance(events[-1].payload, HandoverWritten)
    assert events[-1].payload.summary == "Done: a distilled summary of the work so far"
    assert events[-1].payload.covers_seq_from == 1
    assert events[-1].payload.covers_seq_to == 1
    store.close()


async def test_a_second_handover_only_covers_the_window_after_the_first(tmp_path: Path) -> None:
    store = _configure(
        tmp_path,
        LlmResponse(model="replay", text="Done: first summary of the work so far"),
        LlmResponse(model="replay", text="Done: second summary of the work so far"),
    )
    store.append("task-1", TaskSubmitted(text="x" * 400))
    assert await maybe_handover("task-1", token_budget=50) is True

    store.append("task-1", TaskSubmitted(text="y" * 400))
    assert await maybe_handover("task-1", token_budget=50) is True

    events = list(store.read("task-1"))
    second_handover = events[-1]
    assert isinstance(second_handover.payload, HandoverWritten)
    assert second_handover.payload.summary == "Done: second summary of the work so far"
    # Covers only the new TaskSubmitted (seq 4: the first handover's own summariser call and
    # the handover took seq 2 and 3), not the first one already folded into the first handover.
    assert second_handover.payload.covers_seq_from == 4
    assert second_handover.payload.covers_seq_to == 4
    store.close()


async def test_nothing_to_summarise_after_a_handover_is_a_no_op(tmp_path: Path) -> None:
    store = _configure(
        tmp_path, LlmResponse(model="replay", text="Done: a summary of the work so far")
    )
    store.append("task-1", TaskSubmitted(text="x" * 400))
    assert await maybe_handover("task-1", token_budget=50) is True

    # Nothing new appended since -- the window is empty, regardless of budget.
    assert await maybe_handover("task-1", token_budget=0) is False
    store.close()


async def test_role_filters_the_window_to_that_roles_own_events(tmp_path: Path) -> None:
    """ADR-0007: a team shares one task_id, so a role's own handover must not see,
    or be triggered by, another role's events."""
    store = _configure(
        tmp_path, LlmResponse(model="replay", text="Done: builder summary of the work so far")
    )
    store.append("team-1", TaskSubmitted(text="x" * 4000, role="builder"))
    store.append("team-1", TaskSubmitted(text="y" * 4000, role="reviewer"))

    fired = await maybe_handover("team-1", token_budget=50, role="builder")

    assert fired is True
    events = list(store.read("team-1"))
    handovers = [e for e in events if isinstance(e.payload, HandoverWritten)]
    assert len(handovers) == 1
    assert handovers[0].payload.role == "builder"
    assert handovers[0].payload.covers_seq_from == 1
    assert handovers[0].payload.covers_seq_to == 1  # the reviewer's own event (seq 2) excluded
    store.close()


async def test_one_roles_handover_does_not_suppress_anothers(tmp_path: Path) -> None:
    store = _configure(
        tmp_path,
        LlmResponse(model="replay", text="Done: builder summary of the work so far"),
        LlmResponse(model="replay", text="Done: reviewer summary of the work so far"),
    )
    store.append("team-1", TaskSubmitted(text="x" * 4000, role="builder"))
    store.append("team-1", TaskSubmitted(text="y" * 4000, role="reviewer"))
    assert await maybe_handover("team-1", token_budget=50, role="builder") is True

    # The reviewer's own window is untouched by the builder's handover above.
    assert await maybe_handover("team-1", token_budget=50, role="reviewer") is True

    events = list(store.read("team-1"))
    handovers = [e for e in events if isinstance(e.payload, HandoverWritten)]
    assert {h.payload.role for h in handovers} == {"builder", "reviewer"}
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
    store = _configure(
        tmp_path,
        LlmResponse(model="replay", text="Done: first summary of the work so far"),
        LlmResponse(model="replay", text="Done: second summary of the work so far"),
    )
    store.append("task-1", TaskSubmitted(text="x" * 400))
    assert await maybe_handover("task-1", token_budget=50) is True
    assert await latest_handover_summary("task-1") == "Done: first summary of the work so far"

    store.append("task-1", TaskSubmitted(text="y" * 400))
    assert await maybe_handover("task-1", token_budget=50) is True
    assert await latest_handover_summary("task-1") == "Done: second summary of the work so far"
    store.close()


async def test_latest_handover_summary_is_role_scoped(tmp_path: Path) -> None:
    """The identical role filter `maybe_handover` itself uses (ADR-0007) -- a
    caller reading back one role's own checkpoint must never see another role's."""
    store = _configure(
        tmp_path,
        LlmResponse(model="replay", text="Done: builder summary of the work so far"),
    )
    store.append("team-1", TaskSubmitted(text="x" * 4000, role="builder"))
    assert await maybe_handover("team-1", token_budget=50, role="builder") is True

    assert (
        await latest_handover_summary("team-1", role="builder")
        == "Done: builder summary of the work so far"
    )
    assert await latest_handover_summary("team-1", role="reviewer") is None
    assert await latest_handover_summary("team-1") is None  # role=None is its own lane too
    store.close()


# -- what a handover is made of (ADR-0030, found by the first 16-round real run) ----------------

GOOD = "Done: slug and wrap, committed. Remaining: case, csvline. Nothing failed."


def _kinds(store: EpisodicStore) -> list[str]:
    return [type(event.payload).__name__ for event in store.read("task-1")]


async def test_an_empty_answer_is_asked_for_again_and_both_calls_are_journaled(
    tmp_path: Path,
) -> None:
    store = _configure(
        tmp_path,
        LlmResponse(model="replay", text="   ", input_tokens=10, output_tokens=0),
        LlmResponse(model="replay", text=GOOD, input_tokens=300, output_tokens=40, cost_usd=0.0004),
    )
    store.append("task-1", TaskSubmitted(text="x" * 400))
    assert await maybe_handover("task-1", token_budget=50) is True
    handover = [e.payload for e in store.read("task-1") if isinstance(e.payload, HandoverWritten)]
    assert handover[0].summary == GOOD
    calls = [e.payload for e in store.read("task-1") if isinstance(e.payload, LlmCallCompleted)]
    assert len(calls) == 2  # one retry only, and the cost of each call is in the journal
    assert calls[0].input_tokens == 10 and "third person" in calls[1].prompt
    assert calls[1].output_tokens == 40
    assert calls[0].cost_usd is None and calls[1].cost_usd == 0.0004  # reported, never estimated
    store.close()


async def test_a_second_unusable_answer_carries_the_previous_summary_forward(
    tmp_path: Path,
) -> None:
    store = _configure(
        tmp_path,
        LlmResponse(model="replay", text=GOOD),
        LlmResponse(model="replay", text=""),
        LlmResponse(model="replay", text="I will carry on."),
    )
    store.append("task-1", TaskSubmitted(text="x" * 400))
    await maybe_handover("task-1", token_budget=50)
    store.append("task-1", DelegationCompleted(summary="x" * 400, edited_paths=["case.py"]))
    await maybe_handover("task-1", token_budget=50)
    last = [e.payload for e in store.read("task-1") if isinstance(e.payload, HandoverWritten)][-1]
    assert last.summary.startswith(GOOD)  # never empty, never invented
    assert "carried forward" in last.summary and "case.py" in last.summary
    store.close()


async def test_the_previous_summary_goes_into_the_next_prompt_without_its_repository_state(
    tmp_path: Path,
) -> None:
    from cuttlefish.tasks.repo import REPO_STATE_MARKER

    store = _configure(
        tmp_path,
        LlmResponse(model="replay", text=f"{GOOD}\n\n{REPO_STATE_MARKER}: old\nabc123 stale"),
        LlmResponse(model="replay", text="Done: also case. Remaining: csvline."),
    )
    store.append("task-1", TaskSubmitted(text="x" * 400))
    await maybe_handover("task-1", token_budget=50)
    store.append("task-1", TaskSubmitted(text="y" * 400))
    await maybe_handover("task-1", token_budget=50)
    calls = [e.payload for e in store.read("task-1") if isinstance(e.payload, LlmCallCompleted)]
    assert GOOD in calls[1].prompt and "abc123" not in calls[1].prompt
    store.close()


async def test_the_summarisers_own_call_is_not_progress_to_summarise(tmp_path: Path) -> None:
    from cuttlefish.handover import estimate_event_tokens

    call = LlmCallCompleted(model="m", prompt="p" * 4000, response="r" * 4000)
    assert estimate_event_tokens(call) == 0


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
    store = _configure(
        tmp_path,
        LlmResponse(model="replay", text=GOOD),
        LlmResponse(model="replay", text=GOOD),
    )
    store.append("task-1", TaskSubmitted(text="x" * 400))
    assert await maybe_handover("task-1", token_budget=50, root=str(repo)) is True
    written = [e.payload for e in store.read("task-1") if isinstance(e.payload, HandoverWritten)]
    handover = written[0]
    assert handover.summary.startswith(GOOD)
    assert "feat: slug" in handover.summary and "?? wip.py" in handover.summary
    # Not a git work tree: no state is better than a guess.
    store.append("task-1", TaskSubmitted(text="y" * 400))
    await maybe_handover("task-1", token_budget=50, root=str(tmp_path / "nowhere"))
    last = [e.payload for e in store.read("task-1") if isinstance(e.payload, HandoverWritten)][-1]
    assert last.summary == GOOD
    store.close()
