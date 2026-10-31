"""A tiny fake ACP agent over stdio (CUT-232 spike). Behaviour is chosen by the prompt text:
``edit`` asks to edit ``<cwd>/out.txt``, ``run <cmd>`` asks to execute, ``hang`` waits for
``session/cancel``, anything else just answers. ``FAKE_ACP_AUTH=1`` advertises an auth method."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

_responses: dict[int, dict] = {}
_cond = threading.Condition()
_cancelled = threading.Event()
_next_id = 100


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def update(session: str, body: dict) -> None:
    send(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {"sessionId": session, "update": body},
        }
    )


def ask(session: str, call: dict) -> dict:
    global _next_id
    _next_id += 1
    mid = _next_id
    send(
        {
            "jsonrpc": "2.0",
            "id": mid,
            "method": "session/request_permission",
            "params": {
                "sessionId": session,
                "toolCall": call,
                "options": [
                    {"optionId": "yes", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "no", "name": "Reject", "kind": "reject_once"},
                ],
            },
        }
    )
    with _cond:
        while mid not in _responses:
            _cond.wait()
        return _responses.pop(mid)["result"]["outcome"]


def prompt(mid: int, params: dict) -> None:
    session = params["sessionId"]
    text = params["prompt"][0]["text"]
    cwd = str(Path.cwd())
    update(
        session,
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ok "}},
    )
    if text == "hang":
        _cancelled.wait(10)
        send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "cancelled"}})
        return
    if text == "edit" or text.startswith("run "):
        is_edit = text == "edit"
        call = {
            "toolCallId": "c1",
            "title": "write out.txt" if is_edit else text[4:],
            "kind": "edit" if is_edit else "execute",
            "locations": [{"path": cwd + "/out.txt"}] if is_edit else [],
            "rawInput": {} if is_edit else {"command": text[4:]},
        }
        update(session, {"sessionUpdate": "tool_call", **call, "status": "pending"})
        outcome = ask(session, call)
        if outcome.get("optionId") == "yes":
            if is_edit:
                with (Path(cwd) / "out.txt").open("w") as fh:
                    fh.write("hi")
            update(
                session,
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "c1",
                    "status": "completed",
                    "kind": call["kind"],
                    "locations": call["locations"],
                },
            )
        else:
            update(
                session,
                {"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "failed"},
            )
    update(
        session,
        {
            "sessionUpdate": "usage_update",
            "used": 42,
            "size": 1000,
            "cost": {"amount": 0.01, "currency": "USD"},
        },
    )
    update(
        session,
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "done"}},
    )
    send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})


def main() -> None:
    for line in sys.stdin:
        msg = json.loads(line)
        method = msg.get("method")
        if method is None:
            with _cond:
                _responses[msg["id"]] = msg
                _cond.notify_all()
        elif method == "initialize":
            result: dict = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}
            if os.environ.get("FAKE_ACP_AUTH"):
                result["authMethods"] = [{"id": "login", "name": "Login"}]
            send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        elif method == "session/new":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"sessionId": "s-new"}})
        elif method == "session/load":
            sid = msg["params"]["sessionId"]
            update(
                sid,
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "REPLAYED "},
                },
            )
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        elif method == "session/cancel":
            _cancelled.set()
        elif method == "session/prompt":
            threading.Thread(target=prompt, args=(msg["id"], msg["params"]), daemon=True).start()


if __name__ == "__main__":
    main()
