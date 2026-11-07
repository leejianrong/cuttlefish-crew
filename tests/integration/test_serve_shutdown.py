"""`cuttlefish serve` ends promptly on SIGTERM and SIGINT, and takes its resident agents with it
(CUT-281). The agent is the scripted fake `kopicode serve`, mid-turn and deaf to stdin closing."""

import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from cuttlefish.fleet.server import find_free_port

FAKE = Path(__file__).parents[1] / "unit" / "delegate" / "fake_kopicode_serve.py"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_serve_exits_and_ends_its_resident_agent(sig: signal.Signals, tmp_path: Path) -> None:
    home, project = tmp_path / "home", tmp_path / "proj"
    home.mkdir()
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    pid_log = tmp_path / "sent.jsonl"
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps([{"start": True}, {"sleep": 120}]))
    binary = tmp_path / "kopicode"
    binary.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "HOME": str(home),
        "CUTTLEFISH_KOPICODE_BIN": str(binary),
        "CUTTLEFISH_AGENT_BACKEND": "kopicode",
        "FAKE_KOPICODE_LOG": str(pid_log),
        "FAKE_KOPICODE_SCENARIO": str(scenario),
    }
    serve_out = tmp_path / "serve.log"
    with serve_out.open("w") as out:
        serve = subprocess.Popen(
            [
                str(Path(sys.executable).with_name("cuttlefish")),
                "serve",
                "--port",
                str(find_free_port("127.0.0.1", 18900)),
            ],
            cwd=tmp_path,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 30
        match = None
        while match is None and time.monotonic() < deadline:
            match = re.search(r"http://(\S+)\s+(\S+): (\S+)", serve_out.read_text())
            time.sleep(0.2)
        assert match, serve_out.read_text()
        base, header, token = f"http://{match[1]}", match[2], match[3]

        def call(path: str, body: dict[str, object]) -> dict[str, str]:
            request = urllib.request.Request(
                base + path,
                data=json.dumps(body).encode(),
                headers={header: token, "Content-Type": "application/json"},
            )
            return json.load(urllib.request.urlopen(request, timeout=20))  # type: ignore[no-any-return]

        created = call("/api/projects", {"name": "demo", "root": str(project)})
        call(
            f"/api/projects/{created['id']}/start",
            {"roles": [{"name": "builder", "text": "x"}], "prepare": "skip"},
        )
        agent_pid = 0
        while not agent_pid and time.monotonic() < deadline:
            lines = pid_log.read_text().splitlines() if pid_log.exists() else []
            starts = [json.loads(x) for x in lines if '"pid"' in x]
            agent_pid = next((m["pid"] for m in starts if m["argv"] == ["serve"]), 0)
            time.sleep(0.2)
        assert agent_pid, "the agent never started"

        serve.send_signal(sig)
        started = time.monotonic()
        serve.wait(timeout=25)
        assert time.monotonic() - started < 12
        assert not _alive(agent_pid), "serve left its resident agent running"
    finally:
        serve.kill()
        serve.wait()
