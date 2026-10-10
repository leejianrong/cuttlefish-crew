"""A scripted stand-in for ``claude -p --input-format stream-json --permission-prompt-tool stdio``.

Run as ``fake_claude_stream.py <scenario.json> <log> <claude's own argv...>``. Every line the
client sends, and the argv, is appended to ``<log>``. The scenario is a JSON object; ``steps`` run
after the first user message is read:
  ``{"bash": "<command>"}``    a Bash tool_use, a ``can_use_tool`` request, then a tool_result
  ``{"write": "<abs path>"}``  the same for a Write
  ``{"tool": "<name>"}``       the same for another tool (``WebFetch``)
  ``{"ask": [{"question": "q", "options": ["a", "b"]}]}``  an ``AskUserQuestion`` request
  ``{"api_call": true}``       POST ``$ANTHROPIC_BASE_URL/v1/messages`` with ``$ANTHROPIC_API_KEY``
  ``{"hang": true}``           waits for an ``interrupt`` request, acknowledges it, ends the turn
``crash``: exit with a message on stderr after reading the first message.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

scenario = json.loads(Path(sys.argv[1]).read_text())
log = Path(sys.argv[2]).open("a", buffering=1)  # noqa: SIM115
log.write(json.dumps({"argv": sys.argv[3:]}) + "\n")
log.write(json.dumps({"env": dict(os.environ)}) + "\n")
n = 0


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


def tool_use(name: str, tool_input: dict) -> str:
    global n
    n += 1
    call = f"toolu_{n}"
    send(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "id": call, "name": name, "input": tool_input}]
            },
        }
    )
    return call


def ask(call: str, name: str, tool_input: dict) -> dict:
    request_id = f"req_{call}"
    send(
        {
            "type": "control_request",
            "request_id": request_id,
            "request": {
                "subtype": "can_use_tool",
                "tool_name": name,
                "input": tool_input,
                "tool_use_id": call,
            },
        }
    )
    while True:
        message = read()
        if message.get("type") == "control_response":
            response = message["response"]
            assert response["request_id"] == request_id
            return response["response"]


def tool_result(call: str, text: str, is_error: bool) -> None:
    send(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call,
                        "content": text,
                        "is_error": is_error,
                    }
                ]
            },
        }
    )


read()
send({"type": "system", "subtype": "init", "session_id": "s1"})
if scenario.get("crash"):
    sys.stderr.write("fake claude: boom\n")
    sys.exit(3)
stop = "end_turn"
denials = []  # real Claude Code lists a host's denials in the result too
for step in scenario.get("steps", []):
    if "bash" in step or "write" in step or "tool" in step:
        if "bash" in step:
            name, tool_input = "Bash", {"command": step["bash"]}
        elif "write" in step:
            name, tool_input = "Write", {"file_path": step["write"], "content": "x"}
        else:
            name, tool_input = step["tool"], {"url": "https://example.com"}
        call = tool_use(name, tool_input)
        reply = ask(call, name, tool_input)
        allowed = reply.get("behavior") == "allow"
        if not allowed:
            denials.append({"tool_name": name, "tool_use_id": call})
        tool_result(call, "ok" if allowed else reply.get("message", ""), not allowed)
    elif "ask" in step:
        questions = [
            {
                "question": q["question"],
                "header": "h",
                "multiSelect": False,
                "options": [{"label": o, "description": o} for o in q["options"]],
            }
            for q in step["ask"]
        ]
        call = tool_use("AskUserQuestion", {"questions": questions})
        reply = ask(call, "AskUserQuestion", {"questions": questions})
        allowed = reply.get("behavior") == "allow"
        answers = reply.get("updatedInput", {}).get("answers") if allowed else None
        tool_result(
            call, f"answered {answers}" if allowed else reply.get("message", ""), not allowed
        )
    elif step.get("api_call"):
        request = urllib.request.Request(
            os.environ["ANTHROPIC_BASE_URL"] + "/v1/messages",
            data=b"{}",
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "content-type": "application/json",
            },
        )
        try:
            status = urllib.request.urlopen(request, timeout=10).status
        except urllib.error.HTTPError as exc:
            status = exc.code
        log.write(json.dumps({"api_status": status}) + "\n")
    elif step.get("hang"):
        tool_use("Bash", {"command": "sleep 100"})
        send(
            {
                "type": "control_request",
                "request_id": "pending",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "Bash",
                    "input": {"command": "sleep 100"},
                    "tool_use_id": "toolu_hang",
                },
            }
        )
        while read().get("request", {}).get("subtype") != "interrupt":
            pass
        send(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": "cuttlefish-interrupt",
                    "response": {},
                },
            }
        )
        stop = "tool_use"
send({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}})
send(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "done",
        "stop_reason": stop,
        "permission_denials": denials,
        "session_id": "s1",
        "total_cost_usd": 0.01,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
)
while True:
    read()
