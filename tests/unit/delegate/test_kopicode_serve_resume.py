"""``session.start``'s ``resume`` continues a role's kopicode session over ``serve`` (CUT-113).

A stateful stand-in for ``kopicode serve``: it logs every ``session.start`` it gets, answers with
the journal it would use (the resumed one, else the wire key), and refuses an id listed in
``FAKE_KOPICODE_STALE`` the way kopicode does (-32003).
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from cuttlefish.agents.kopicode import KopicodeBackend
from cuttlefish.delegate import conversations, kopicode_serve
from cuttlefish.delegate.kopicode_serve import ServePool

_FAKE = """#!{python}
import json, os, sys
log = open(os.environ["FAKE_KOPICODE_LOG"], "a")
if sys.argv[1:2] == ["version"]:
    print(json.dumps({{"features": os.environ.get("FAKE_KOPICODE_FEATURES", "").split(",")}}))
    sys.exit(0)

def out(msg):
    sys.stdout.write(json.dumps(msg) + "\\n")
    sys.stdout.flush()

for raw in sys.stdin:
    msg = json.loads(raw)
    p = msg.get("params", {{}})
    if msg.get("method") == "session.start":
        log.write(json.dumps(p) + "\\n"); log.flush()
        stale = os.environ.get("FAKE_KOPICODE_STALE")
        if p.get("resume") and p["resume"] == stale:
            out({{"jsonrpc": "2.0", "id": msg["id"], "error": {{"code": -32003,
                  "message": "no session " + repr(p["resume"]) + " to resume"}}}})
            continue
        journal = p.get("resume") or p["session"]
        out({{"jsonrpc": "2.0", "id": msg["id"], "result": {{"session": p["session"],
              "record": os.getcwd() + "/.kopicode/sessions/" + journal,
              "stop": "completed", "exit_code": 0, "turns": 1}}}})
    elif msg.get("method") == "session.close":
        out({{"jsonrpc": "2.0", "method": "session.event", "params": {{"session": p["session"],
              "event": {{"kind": "session_ended", "reason": "completed", "exit_code": 0}}}}}})
        out({{"jsonrpc": "2.0", "id": msg["id"],
              "result": {{"session": p["session"], "closed": True}}}})
"""


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    binary = tmp_path / "kopicode"
    binary.write_text(_FAKE.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_KOPICODE_LOG", str(tmp_path / "starts.log"))
    monkeypatch.setenv("FAKE_KOPICODE_FEATURES", "session.close,session.resume")
    monkeypatch.setenv("CUTTLEFISH_AGENT_ENV_PASSTHROUGH", "FAKE_KOPICODE_*")
    monkeypatch.setenv("CUTTLEFISH_RESUME", "1")
    kopicode_serve._FEATURES.pop(str(binary), None)
    return str(binary)


def _starts(tmp_path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (tmp_path / "starts.log").read_text().splitlines()]


async def _round(binary: str, root: Path, pool: ServePool, role: str | None = "dev") -> str | None:
    outcome = await KopicodeBackend(binary, transport="serve", pool=pool).delegate(
        task_text="go",
        root=str(root),
        allow=None,
        secrets={},
        sandbox_provider=None,
        session=role,
    )
    assert outcome.kind == "completed", outcome
    return outcome.record


async def test_the_second_round_resumes_the_first_over_serve(fake: str, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    pool = ServePool()
    try:
        first = await _round(fake, root, pool)
        await _round(fake, root, pool)
    finally:
        await pool.aclose()
    one, two = _starts(tmp_path)
    assert "resume" not in one
    assert first and two["resume"] == Path(first).name
    assert two["session"] != one["session"]  # the wire key is each round's own
    assert conversations.recall(str(root), "kopicode", "dev") == Path(first).name


async def test_the_session_id_survives_a_new_process(fake: str, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    pool = ServePool()
    try:
        first = await _round(fake, root, pool)
    finally:
        await pool.aclose()
    fresh = ServePool()  # a daemon restart: no resident child, only conversations.json
    try:
        await _round(fake, root, fresh)
    finally:
        await fresh.aclose()
    assert first and _starts(tmp_path)[-1]["resume"] == Path(first).name


async def test_a_session_kopicode_lost_is_replaced(
    fake: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    conversations.remember(str(root), "kopicode", "dev", "gone")
    monkeypatch.setenv("FAKE_KOPICODE_STALE", "gone")
    pool = ServePool()
    try:
        record = await _round(fake, root, pool)
    finally:
        await pool.aclose()
    tried, retried = _starts(tmp_path)
    assert tried["resume"] == "gone" and "resume" not in retried
    assert record and conversations.recall(str(root), "kopicode", "dev") == Path(record).name


async def test_a_kopicode_without_the_feature_starts_new_sessions(
    fake: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_KOPICODE_FEATURES", "session.close")
    root = tmp_path / "proj"
    root.mkdir()
    pool = ServePool()
    try:
        await _round(fake, root, pool)
        await _round(fake, root, pool)
    finally:
        await pool.aclose()
    assert all("resume" not in s for s in _starts(tmp_path))
    assert conversations.recall(str(root), "kopicode", "dev") is None


async def test_resume_is_off_unless_asked_for(
    fake: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CUTTLEFISH_RESUME")
    root = tmp_path / "proj"
    root.mkdir()
    pool = ServePool()
    try:
        await _round(fake, root, pool)
        await _round(fake, root, pool)
    finally:
        await pool.aclose()
    assert all("resume" not in s for s in _starts(tmp_path))
