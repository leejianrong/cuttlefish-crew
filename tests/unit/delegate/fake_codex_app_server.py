"""A scripted stand-in for ``codex app-server`` (stdin/stdout NDJSON JSON-RPC, no ``jsonrpc``).

Run as ``fake_codex_app_server.py app-server <scenario.json> <log>``. Every line the client sends
is appended to ``<log>``. The scenario is a JSON object:

``steps``: a list run after ``turn/start`` is answered --
  ``{"command": "<command>", "cwd": "<dir>"}`` sends ``item/commandExecution/requestApproval``,
  waits for the reply and completes the item as ``declined`` or ``completed`` to match it;
  ``{"file_change": "<abs path>"}`` completes a ``fileChange`` item;
  ``{"file_approval": [<paths>], "grant_root": <dir>?}`` starts a ``fileChange`` item and sends
  ``item/fileChange/requestApproval`` for it, completing it if the reply accepts;
  ``{"request": "<method>", "params": {...}}`` sends that server request and waits for the reply;
  ``{"hang": true}`` waits for ``turn/interrupt``, answers it and ends the turn ``interrupted``.
``final``: the final answer text (default ``done``). ``crash``: exit at once after the handshake.
"""

import json
import sys
from pathlib import Path

scenario = json.loads(Path(sys.argv[2]).read_text())
log = Path(sys.argv[3]).open("a", buffering=1)  # noqa: SIM115
next_id = 1000


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def read() -> dict:
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    message = json.loads(line)
    log.write(json.dumps(message) + "\n")
    return message


def read_until(match) -> dict:
    while True:
        message = read()
        if match(message):
            return message


def ask(method: str, params: dict) -> dict:
    global next_id
    next_id += 1
    send({"id": next_id, "method": method, "params": params})
    return read_until(lambda m: m.get("id") == next_id and "method" not in m)["result"]


def answer(request: dict, result: dict) -> None:
    send({"id": request["id"], "result": result})


answer(read_until(lambda m: m.get("method") == "initialize"), {"userAgent": "fake/0"})
read_until(lambda m: m.get("method") == "initialized")
if scenario.get("crash"):
    sys.stderr.write("fake codex: boom\n")
    sys.exit(3)
answer(read_until(lambda m: m.get("method") == "thread/start"), {"thread": {"id": "th1"}})
answer(
    read_until(lambda m: m.get("method") == "turn/start"),
    {"turn": {"id": "tu1", "status": "inProgress", "items": []}},
)
status = "completed"
for step in scenario.get("steps", []):
    if "command" in step:
        command = step["command"]
        reply = ask(
            "item/commandExecution/requestApproval",
            {
                "threadId": "th1",
                "turnId": "tu1",
                "itemId": "i",
                "command": command,
                "cwd": step.get("cwd", "."),
                "availableDecisions": ["accept", "cancel"],
            },
        )
        accepted = reply.get("decision") == "accept"
        send(
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "commandExecution",
                        "id": "i",
                        "command": command,
                        "status": "completed" if accepted else "declined",
                        "exitCode": 0 if accepted else None,
                    }
                },
            }
        )
    elif "file_change" in step:
        send(
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "fileChange",
                        "id": "f",
                        "status": "completed",
                        "changes": [
                            {"path": step["file_change"], "kind": {"type": "add"}, "diff": ""}
                        ],
                    }
                },
            }
        )
    elif "file_approval" in step:
        changes = [{"path": p, "kind": {"type": "add"}, "diff": ""} for p in step["file_approval"]]
        item = {"type": "fileChange", "id": "f", "changes": changes}
        send({"method": "item/started", "params": {"item": {**item, "status": "inProgress"}}})
        params = {"threadId": "th1", "turnId": "tu1", "itemId": "f", "startedAtMs": 0}
        if step.get("grant_root"):
            params["grantRoot"] = step["grant_root"]
        reply = ask("item/fileChange/requestApproval", params)
        if reply.get("decision") == "accept":
            send({"method": "item/completed", "params": {"item": {**item, "status": "completed"}}})
    elif "request" in step:
        ask(step["request"], step.get("params", {}))
    elif step.get("hang"):
        interrupt = read_until(lambda m: m.get("method") == "turn/interrupt")
        answer(interrupt, {})
        status = "interrupted"
send(
    {
        "method": "thread/tokenUsage/updated",
        "params": {
            "tokenUsage": {"total": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120}}
        },
    }
)
send(
    {
        "method": "item/completed",
        "params": {
            "item": {
                "type": "agentMessage",
                "id": "m",
                "phase": "final_answer",
                "text": scenario.get("final", "done"),
            }
        },
    }
)
send(
    {
        "method": "turn/completed",
        "params": {"threadId": "th1", "turn": {"id": "tu1", "status": status, "error": None}},
    }
)
read_until(lambda m: False)
