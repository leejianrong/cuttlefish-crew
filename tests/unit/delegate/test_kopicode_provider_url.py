"""``CUTTLEFISH_KOPICODE_PROVIDER_URL`` points kopicode at a custom endpoint (CUT-116).

Stand-ins for kopicode: one speaks ``serve`` (logs its argv and each ``session.start``), one is
``run --print`` (logs its argv). ``version --json`` answers with the features in
``FAKE_KOPICODE_FEATURES``.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from cuttlefish.agents.kopicode import KopicodeBackend
from cuttlefish.agents.outcome import DelegationError
from cuttlefish.delegate import kopicode_serve
from cuttlefish.delegate.kopicode import build_kopicode_argv
from cuttlefish.delegate.kopicode_serve import ServePool

_FAKE = """#!{python}
import json, os, sys
log = open(os.environ["FAKE_KOPICODE_LOG"], "a")
if sys.argv[1:2] == ["version"]:
    print(json.dumps({{"features": os.environ.get("FAKE_KOPICODE_FEATURES", "").split(",")}}))
    sys.exit(0)
log.write(json.dumps({{"argv": sys.argv[1:], "env": sorted(os.environ)}}) + "\\n"); log.flush()
if sys.argv[1:2] == ["run"]:
    print(json.dumps({{"kind": "session_started", "detail": "ses-1"}}))
    print(json.dumps({{"kind": "session_ended", "reason": "completed", "exit_code": 0}}))
    sys.exit(0)

def out(msg):
    sys.stdout.write(json.dumps(msg) + "\\n")
    sys.stdout.flush()

for raw in sys.stdin:
    msg = json.loads(raw)
    p = msg.get("params", {{}})
    if msg.get("method") == "session.start":
        log.write(json.dumps({{"start": p}}) + "\\n"); log.flush()
        out({{"jsonrpc": "2.0", "id": msg["id"], "result": {{"session": p["session"],
              "record": os.getcwd() + "/.kopicode/sessions/" + p["session"],
              "stop": "completed", "exit_code": 0, "turns": 1}}}})
    elif msg.get("method") == "session.close":
        out({{"jsonrpc": "2.0", "method": "session.event", "params": {{"session": p["session"],
              "event": {{"kind": "session_ended", "reason": "completed", "exit_code": 0}}}}}})
        out({{"jsonrpc": "2.0", "id": msg["id"],
              "result": {{"session": p["session"], "closed": True}}}})
"""

URL = "http://127.0.0.1:11434/v1"


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    binary = tmp_path / "kopicode"
    binary.write_text(_FAKE.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_KOPICODE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FAKE_KOPICODE_FEATURES", "session.close,provider_url.flag")
    monkeypatch.setenv("CUTTLEFISH_AGENT_ENV_PASSTHROUGH", "FAKE_KOPICODE_*")
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_PROVIDER_URL", URL)
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_MODEL", "llama3.2")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "0" * 40)
    monkeypatch.setenv("KOPICODE_PROVIDER_API_KEY", "local-key")
    kopicode_serve._FEATURES.pop(str(binary), None)
    return str(binary)


def _calls(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "calls.log"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


async def _round(binary: str, root: Path, transport: str, pool: ServePool | None = None) -> None:
    backend = KopicodeBackend(binary, transport=transport, pool=pool)  # type: ignore[arg-type]
    outcome = await backend.delegate(
        task_text="go", root=str(root), allow=None, secrets={}, sandbox_provider=None
    )
    assert outcome.kind == "completed", outcome


def test_argv_carries_the_url_and_model_before_the_task() -> None:
    assert build_kopicode_argv("k", "go", provider_url=URL, model="m")[:7] == [
        "k",
        "run",
        "--print",
        "--provider-url",
        URL,
        "--model",
        "m",
    ]


async def test_serve_gets_the_flag_and_session_start_the_model(fake: str, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    pool = ServePool()
    try:
        await _round(fake, root, "serve", pool)
    finally:
        await pool.aclose()
    spawn, start = [c for c in _calls(tmp_path) if "argv" in c or "start" in c]
    assert spawn["argv"] == ["serve", "--provider-url", URL]
    assert start["start"]["model"] == "llama3.2"  # type: ignore[index]
    env = spawn["env"]
    assert "KOPICODE_PROVIDER_API_KEY" in env  # type: ignore[operator]
    assert "OPENROUTER_API_KEY" not in env  # type: ignore[operator]


async def test_print_gets_the_flags(fake: str, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    await _round(fake, root, "print")
    (call,) = _calls(tmp_path)
    argv = call["argv"]
    assert "--provider-url" in argv and URL in argv and "llama3.2" in argv  # type: ignore[operator]
    assert "OPENROUTER_API_KEY" not in call["env"]  # type: ignore[operator]


async def test_a_pooled_child_is_not_reused_across_urls(
    fake: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    pool = ServePool()
    try:
        await _round(fake, root, "serve", pool)
        monkeypatch.setenv("CUTTLEFISH_KOPICODE_PROVIDER_URL", "http://127.0.0.1:8080/v1")
        await _round(fake, root, "serve", pool)
    finally:
        await pool.aclose()
    spawns = [c["argv"] for c in _calls(tmp_path) if "argv" in c]
    assert spawns == [
        ["serve", "--provider-url", URL],
        ["serve", "--provider-url", "http://127.0.0.1:8080/v1"],
    ]


async def test_no_url_leaves_everything_as_it_was(
    fake: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CUTTLEFISH_KOPICODE_PROVIDER_URL")
    root = tmp_path / "proj"
    root.mkdir()
    pool = ServePool()
    try:
        await _round(fake, root, "serve", pool)
    finally:
        await pool.aclose()
    spawn = next(c for c in _calls(tmp_path) if "argv" in c)
    assert spawn["argv"] == ["serve"] and "OPENROUTER_API_KEY" in spawn["env"]  # type: ignore[operator]
    assert "model" not in next(c for c in _calls(tmp_path) if "start" in c)["start"]  # type: ignore[operator]


@pytest.mark.parametrize(
    ("model", "features", "message"),
    [
        ("", "session.close,provider_url.flag", "CUTTLEFISH_KOPICODE_MODEL"),
        ("llama3.2", "session.close", "provider_url.flag"),
    ],
)
async def test_a_half_configured_endpoint_is_refused_before_anything_runs(
    fake: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    features: str,
    message: str,
) -> None:
    monkeypatch.setenv("CUTTLEFISH_KOPICODE_MODEL", model)
    monkeypatch.setenv("FAKE_KOPICODE_FEATURES", features)
    with pytest.raises(DelegationError, match=message):
        await _round(fake, tmp_path, "serve", ServePool())
    assert _calls(tmp_path) == []  # only the version probe ran, which logs nothing
