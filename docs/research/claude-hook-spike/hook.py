#!/usr/bin/env python3
"""Spike hook: log every event, and for PermissionRequest act on the mode in ./mode ('none', 'allow D', 'deny D',
'hang', 'fail', 'exit2')."""
import json, os, signal, sys, time
H = os.path.dirname(os.path.abspath(__file__))
payload = json.load(sys.stdin)
ev = payload.get("hook_event_name")
def log(**k):
    with open(H + "/hook.log", "a") as f:
        f.write(json.dumps({"t": round(time.time(), 2), "ev": ev, "pid": os.getpid(), **k}) + "\n")
signal.signal(signal.SIGTERM, lambda *a: (log(note="SIGTERM received"), sys.exit(143)))
slim = {k: payload.get(k) for k in ("tool_name", "tool_input", "notification_type", "message", "permission_mode", "session_id") if k in payload}
slim["keys"] = sorted(payload)
log(note="start", payload=slim)
if ev != "PermissionRequest":
    sys.exit(0)
parts = open(H + "/mode").read().split() or ["none"]
mode, delay = parts[0], float(parts[1]) if len(parts) > 1 else 0
if mode in ("allow", "deny"):
    time.sleep(delay)
    out = {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": mode}}}
    if mode == "deny":
        out["hookSpecificOutput"]["decision"]["message"] = "Denied from the spike hook"
    print(json.dumps(out)); log(note="decided " + mode)
elif mode == "hang":
    time.sleep(120); log(note="hang finished")
elif mode == "fail":
    print("boom", file=sys.stderr); log(note="exit 1"); sys.exit(1)
elif mode == "exit2":
    print("two", file=sys.stderr); log(note="exit 2"); sys.exit(2)
else:
    log(note="no decision, exit 0")
