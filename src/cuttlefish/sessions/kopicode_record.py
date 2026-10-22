"""A chat thread read from kopicode's own session record (CUT-178, ADR-0044).

kopicode journals every session to ``<repo>/.kopicode/sessions/<id>/events.jsonl``: one JSON
object per line with a ``type`` and a ``payload``. The ones that make a conversation are
``UserMessage``, ``AssistantMessage``, ``ToolCallParsed`` with its ``ToolResult``, and
``AskRequested`` (a question the agent put to the person); provider traffic, permission and
verification lines are left out (a permission shows as a Needs you card). Large text is
``{"inline": ...}``, and anything spilled to a blob is shown as a note of its size. The same chat
items as Claude Code's and Codex's (:mod:`cuttlefish.sessions.transcript`) come out, so the page has
one path.

The REPL kopicode runs in tmux creates its own session id, which cuttlefish does not know, so the
record is found by folder and time: the newest session under the project's ``.kopicode/sessions``
whose events were written after the tmux session started. Observed on kopicode v0.5.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cuttlefish.sessions.transcript import _clip, _one_line, read_thread

_OUTPUT_CHARS = 2000
_TEXT_CHARS = 20000

#: kopicode's tool names as the page's call rows know them (the dashboard groups by these).
_TOOL_NAMES = {
    "run_shell": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "apply_patch": "Edit",
    "list_dir": "LS",
    "search": "Grep",
    "grep": "Grep",
    "glob": "Glob",
    "ask": "Ask",
}

#: Every other type is provider traffic or bookkeeping.
_SHOWN = frozenset(
    {"UserMessage", "AssistantMessage", "ToolCallParsed", "ToolResult", "AskRequested"}
)
_IGNORED = frozenset(
    {
        "SessionStarted", "SessionEnded", "ProviderRequest", "ProviderResponse", "ProviderRetried",
        "ToolCallRequested", "PermissionRequested", "PermissionDecided", "AskAnswered",
        "TurnSnapshot", "VerificationRun",
    }
)  # fmt: skip


def find_events(root: str, *, not_before: float = 0.0) -> Path | None:
    """The newest ``events.jsonl`` under the project's ``.kopicode/sessions`` written at or after
    ``not_before`` (seconds since the epoch), or ``None``."""
    base = Path(root) / ".kopicode" / "sessions"
    try:
        folders = list(base.iterdir())
    except OSError:
        return None
    best: tuple[float, Path] | None = None
    for folder in folders:
        path = folder / "events.jsonl"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= not_before and (best is None or mtime > best[0]):
            best = (mtime, path)
    return best[1] if best else None


def _text(value: object) -> str:
    """A ``{"inline": text, "size": n}`` field's text, or a note when it was spilled to a blob."""
    if not isinstance(value, Mapping):
        return ""
    inline = value.get("inline")
    if isinstance(inline, str):
        return inline
    size = value.get("size")
    return f"(a large output, {size} bytes, kept in kopicode's own record)" if size else ""


def _summary(tool: str, args: object) -> str:
    if isinstance(args, Mapping):
        for key in ("command", "cmd", "path", "file_path", "pattern", "query"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return _one_line(value)
    return ""


def items_for(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The chat items one ``events.jsonl`` line stands for (none for bookkeeping)."""
    kind = entry.get("type")
    payload = entry.get("payload")
    if kind not in _SHOWN or not isinstance(payload, Mapping):
        return []
    at = entry.get("ts") if isinstance(entry.get("ts"), str) else None
    ident = f"{entry.get('session_id')}:{entry.get('seq')}"
    if kind in ("UserMessage", "AssistantMessage"):
        text = _text(payload.get("text"))
        if not text.strip():
            return []
        who = "user" if kind == "UserMessage" else "assistant"
        return [{"id": ident, "kind": who, "at": at, "text": _clip(text, _TEXT_CHARS)}]
    if kind == "AskRequested":
        question = _text(payload.get("question"))
        return (
            [{"id": ident, "kind": "assistant", "at": at, "text": _clip(question, _TEXT_CHARS)}]
            if question
            else []
        )
    call_id = payload.get("call_id")
    if not isinstance(call_id, str):
        return []
    tool = str(payload.get("tool") or "tool")
    if kind == "ToolCallParsed":
        name = _TOOL_NAMES.get(tool, tool)
        return [{"id": ident, "kind": "tool", "at": at, "tool_id": call_id, "name": name,
                 "summary": _summary(tool, payload.get("args"))}]  # fmt: skip
    return [{"id": ident, "kind": "tool_result", "at": at, "tool_id": call_id,
             "text": _clip(_text(payload.get("output")), _OUTPUT_CHARS),
             "is_error": bool(payload.get("error_kind"))}]  # fmt: skip


def read_events(path: Path, after: int | None, *, scrub: Any = lambda text: text) -> Any:
    """Incremental read of an ``events.jsonl``, as :func:`~...transcript.read_thread`."""
    return read_thread(path, after, scrub=scrub, parse=items_for, ignored=_IGNORED)
