"""A scripted stand-in for ``kopicode serve`` (stdin/stdout NDJSON JSON-RPC).

Run as ``fake_kopicode_serve.py serve``; the scenario is a JSON list of steps in the file
named by ``FAKE_KOPICODE_SCENARIO``, and every line the client sends is appended to the
file named by ``FAKE_KOPICODE_LOG`` (one JSON object per line, or ``{"eof": true}``).

Steps: ``{"start": true}`` reads the client's session.start; ``{"emit": <msg>}`` writes a
line (``"$session"``/``"$start_id"`` are substituted); ``{"consent": {...}, "wait": secs}``
sends a consent.request and records the reply (or ``"timeout"`` after ``wait`` seconds);
``{"wait_for": "session.cancel"}`` blocks until that method arrives; ``{"close": [<msg>...]}``
waits for session.close, writes the messages (``session_ended``), then acknowledges it;
``{"eof": [<msg>...]}`` blocks until stdin closes, then writes the messages (shutdown's
``session_ended``).
"""

import json
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Any

scenario = json.loads(Path(os.environ["FAKE_KOPICODE_SCENARIO"]).read_text())
log = Path(os.environ["FAKE_KOPICODE_LOG"]).open("a", buffering=1)  # noqa: SIM115
lines: queue.Queue[dict[str, Any] | None] = queue.Queue()


def _pump() -> None:
    for raw in sys.stdin:
        msg = json.loads(raw)
        log.write(json.dumps(msg) + "\n")
        lines.put(msg)
    log.write(json.dumps({"eof": True}) + "\n")
    lines.put(None)


log.write(json.dumps({"pid": os.getpid(), "argv": sys.argv[1:]}) + "\n")
threading.Thread(target=_pump, daemon=True).start()
ctx: dict[str, Any] = {}


def out(msg: dict[str, Any]) -> None:
    text = json.dumps(msg).replace('"$session"', json.dumps(ctx.get("session", "")))
    text = text.replace('"$start_id"', json.dumps(ctx.get("start_id")))
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


for step in scenario:
    if "start" in step:
        msg = lines.get()
        assert msg and msg["method"] == "session.start", msg
        ctx["start_id"], ctx["session"] = msg["id"], msg["params"]["session"]
    elif "emit" in step:
        out(step["emit"])
    elif "consent" in step:
        out(
            {
                "jsonrpc": "2.0",
                "id": step["consent"]["id"],
                "method": "consent.request",
                "params": {
                    "session": "$session",
                    "reason": "",
                    "resolved": "",
                    "tool": "run_shell",
                    **{k: v for k, v in step["consent"].items() if k != "id"},
                },
            }
        )
        reply: dict[str, Any] | str | None
        try:
            reply = lines.get(timeout=step.get("wait", 5))
        except queue.Empty:
            reply = "timeout"
        log.write(json.dumps({"reply_seen": reply}) + "\n")
    elif "wait_for" in step:
        while (msg := lines.get()) is not None and msg.get("method") != step["wait_for"]:
            pass
    elif "close" in step:
        while (msg := lines.get()) is not None and msg.get("method") != "session.close":
            pass
        assert msg is not None, "stdin closed before session.close"
        for emitted in step["close"]:
            out(emitted)
        out(
            {
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {"session": ctx["session"], "closed": True},
            }
        )
    elif "exit" in step:
        os._exit(0)
    elif "eof" in step:
        while lines.get() is not None:
            pass
        for msg in step["eof"]:
            out(msg)
