"""A tiny fake ACP agent over stdio (CUT-232 spike). Behaviour is chosen by the prompt text:
``edit`` asks to edit ``<cwd>/out.txt``, ``run <cmd>`` asks to execute, ``hang`` waits for
``session/cancel``, anything else just answers. ``FAKE_ACP_AUTH=1`` advertises an auth method
and works anyway, ``required`` answers ``session/new`` with auth_required until ``authenticate``
is called, ``fail`` never lets it in. ``fsread <path>``, ``fswrite <path>`` and ``term <cmd>`` use
the client's file and terminal methods and report each reply as a message chunk. A handoff request
(cuttlefish's prompt) is answered with a document, or plainly when ``FAKE_ACP_HANDOFF=none``."""

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


def call(method: str, params: dict) -> dict:
    """A request to the client; its whole reply (``result`` or ``error``)."""
    global _next_id
    _next_id += 1
    mid = _next_id
    send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
    with _cond:
        while mid not in _responses:
            _cond.wait()
        return _responses.pop(mid)


def client_methods(session: str, text: str) -> str:
    verb, _, arg = text.partition(" ")
    if verb == "fsread":
        reply = call("fs/read_text_file", {"sessionId": session, "path": arg})
    elif verb == "fswrite":
        reply = call("fs/write_text_file", {"sessionId": session, "path": arg, "content": "via"})
    else:
        reply = call(
            "terminal/create", {"sessionId": session, "command": "/bin/sh", "args": ["-c", arg]}
        )
        if "result" in reply:
            tid = reply["result"]["terminalId"]
            call("terminal/wait_for_exit", {"sessionId": session, "terminalId": tid})
            reply = call("terminal/output", {"sessionId": session, "terminalId": tid})
            call("terminal/release", {"sessionId": session, "terminalId": tid})
    if "error" in reply:
        return "ERR(" + reply["error"]["message"] + ")"
    return "OK(" + json.dumps(reply["result"]) + ")"


def prompt(mid: int, params: dict) -> None:
    session = params["sessionId"]
    text = params["prompt"][0]["text"].strip().splitlines()[-1]  # cuttlefish prefixes a note
    if text.startswith("Your round has ended"):
        body = (
            "just talking"
            if os.environ.get("FAKE_ACP_HANDOFF") == "none"
            else "## Done\nnothing\n## Remaining\neverything\n## Failed or refused\nnone\n"
            "## Open questions\nnone"
        )
        update(
            session,
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": body}},
        )
        send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
        return
    if text.split(" ")[0] in ("fsread", "fswrite", "term"):
        out = client_methods(session, text)
        update(
            session,
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": out}},
        )
        send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
        return
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
    signed_in = False
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
        elif method == "authenticate":
            if os.environ.get("FAKE_ACP_AUTH") == "fail":
                send(
                    {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": "no"}}
                )
            else:
                signed_in = True
                send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        elif method == "session/new":
            if os.environ.get("FAKE_ACP_AUTH") in ("required", "fail") and not signed_in:
                error = {"code": -32000, "message": "Authentication required"}
                send({"jsonrpc": "2.0", "id": msg["id"], "error": error})
            else:
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
