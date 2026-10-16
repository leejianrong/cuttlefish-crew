"""``run --print -resume`` continues a role's kopicode session across rounds (CUT-94).

A scripted stand-in for ``kopicode run --print``: it logs its argv, announces a session on
``session_started`` and ends cleanly. ``kopicode serve`` has no resume, so only this transport
continues (KOP-207).
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from cuttlefish.delegate import conversations
from cuttlefish.delegate.kopicode import build_kopicode_argv, run_kopicode, session_id_of

_FAKE = """#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_KOPICODE_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
stale = os.environ.get("FAKE_KOPICODE_STALE")
if "-resume" in args and stale and args[args.index("-resume") + 1] == stale:
    sys.stderr.write("no such session\\n")
    sys.exit(2)
sid = args[args.index("-resume") + 1] if "-resume" in args else os.environ["FAKE_KOPICODE_NEW"]
print(json.dumps({{"kind": "session_started", "detail": sid}}))
print(json.dumps({{"kind": "edit_applied", "path": "a.txt"}}))
print(json.dumps({{"kind": "session_ended", "reason": "completed", "exit_code": 0}}))
"""


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binary = tmp_path / "kopicode"
    binary.write_text(_FAKE.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_KOPICODE_LOG", str(tmp_path / "argv.log"))
    monkeypatch.setenv("FAKE_KOPICODE_NEW", "ses-1")
    return binary


def _argvs(tmp_path: Path) -> list[list[str]]:
    return [json.loads(line) for line in (tmp_path / "argv.log").read_text().splitlines()]


def test_argv_carries_resume_before_the_task() -> None:
    assert build_kopicode_argv("kopicode", "go", resume="ses-9") == [
        "kopicode",
        "run",
        "--print",
        "-resume",
        "ses-9",
        "go",
    ]


def test_session_id_is_read_from_session_started() -> None:
    assert session_id_of('{"kind":"stream"}\n{"kind":"session_started","detail":"abc"}\n') == "abc"
    assert session_id_of('{"kind":"session_ended"}\n') is None


async def test_the_second_round_resumes_the_first_session(fake: Path, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    for _ in range(2):
        outcome = await run_kopicode(
            binary=str(fake), task_text="go", root=str(root), conversation="dev"
        )
        assert outcome.kind == "completed"
    first, second = _argvs(tmp_path)
    assert "-resume" not in first
    assert second[second.index("-resume") + 1] == "ses-1"
    assert conversations.recall(str(root), "kopicode", "dev") == "ses-1"


async def test_two_roles_keep_their_own_sessions(
    fake: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    await run_kopicode(binary=str(fake), task_text="go", root=str(root), conversation="dev")
    monkeypatch.setenv("FAKE_KOPICODE_NEW", "ses-2")
    await run_kopicode(binary=str(fake), task_text="go", root=str(root), conversation="qa")
    assert [a for a in _argvs(tmp_path) if "-resume" in a] == []
    assert conversations.recall(str(root), "kopicode", "qa") == "ses-2"
    assert conversations.recall(str(root), "kopicode", "dev") == "ses-1"


async def test_no_conversation_never_resumes_or_remembers(fake: Path, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    for _ in range(2):
        await run_kopicode(binary=str(fake), task_text="go", root=str(root))
    assert all("-resume" not in a for a in _argvs(tmp_path))
    assert conversations.recall(str(root), "kopicode", "dev") is None


async def test_a_session_kopicode_lost_is_replaced_by_a_new_one(
    fake: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    conversations.remember(str(root), "kopicode", "dev", "gone")
    monkeypatch.setenv("FAKE_KOPICODE_STALE", "gone")
    outcome = await run_kopicode(
        binary=str(fake), task_text="go", root=str(root), conversation="dev"
    )
    assert outcome.kind == "completed"
    tried, retried = _argvs(tmp_path)
    assert "gone" in tried and "-resume" not in retried
    assert conversations.recall(str(root), "kopicode", "dev") == "ses-1"


async def test_the_backend_resumes_over_the_print_transport_only_when_asked(
    fake: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cuttlefish.agents.kopicode import KopicodeBackend

    monkeypatch.setenv("CUTTLEFISH_KOPICODE_TRANSPORT", "print")
    monkeypatch.setenv("CUTTLEFISH_AGENT_ENV_PASSTHROUGH", "FAKE_KOPICODE_*")
    root = tmp_path / "proj"
    root.mkdir()
    backend = KopicodeBackend(str(fake))

    async def round_(session: str | None) -> None:
        await backend.delegate(
            task_text="go",
            root=str(root),
            allow=None,
            secrets={},
            sandbox_provider=None,
            session=session,
        )

    await round_("dev")
    await round_("dev")
    assert all("-resume" not in a for a in _argvs(tmp_path))  # off without CUTTLEFISH_RESUME

    monkeypatch.setenv("CUTTLEFISH_RESUME", "1")
    await round_("dev")
    await round_("dev")
    assert "-resume" in _argvs(tmp_path)[-1]
