"""A chat thread read from Codex's own session record (CUT-177, ADR-0044).

Codex writes each session to ``~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<session id>.jsonl``: one
JSON object per line, and the ones that matter are ``event_msg`` lines whose payload is an
``item_completed`` (a ``UserMessage``, an ``AgentMessage``, a ``CommandExecution``, a
``FileChange``, a web search, a ``ContextCompaction``). They become the same chat items the Claude
Code reader makes (:mod:`cuttlefish.sessions.transcript`), so the page does not care which tool it
is reading. The format is Codex's and not documented as stable (observed on codex-cli 0.155.1);
what is not understood is left out.

A tmux-hosted Codex is started by ``codex`` or ``codex resume --last``, so its session id is not
known to cuttlefish. The record is found by the folder instead: the newest rollout whose first line
(``session_meta``) names the project's root as its ``cwd``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cuttlefish.sessions.transcript import _clip, _one_line, read_thread

#: How many days back a record is looked for, and how many candidate files are opened.
_DAYS = 14
_CANDIDATES = 40
_OUTPUT_CHARS = 2000

_IGNORED = frozenset(
    {
        "session_meta",
        "world_state",
        "turn_context",
        "token_usage_record",
        "response_item",
        "compacted",
    }
)


def sessions_dir(home: Path | None = None) -> Path:
    return (home if home is not None else Path.home()) / ".codex" / "sessions"


def _cwd_of(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            first = json.loads(handle.readline())
    except (OSError, ValueError):
        return None
    payload = first.get("payload") if isinstance(first, dict) else None
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    return cwd if isinstance(cwd, str) else None


def find_rollout(root: str, *, home: Path | None = None, not_before: float = 0.0) -> Path | None:
    """The newest recent rollout whose ``cwd`` is ``root``, or ``None``. ``not_before`` (seconds
    since the epoch) skips records last written before the session was started."""
    base = sessions_dir(home)
    wanted = {str(Path(root).resolve()), root}
    today = datetime.now(UTC)
    found: list[tuple[float, Path]] = []
    for back in range(_DAYS + 1):
        day = base / (today - timedelta(days=back)).strftime("%Y/%m/%d")
        try:
            names = list(day.iterdir())
        except OSError:
            continue
        for path in names:
            if path.name.startswith("rollout-") and path.name.endswith(".jsonl"):
                try:
                    found.append((path.stat().st_mtime, path))
                except OSError:
                    continue
    found.sort(reverse=True)
    for mtime, path in found[:_CANDIDATES]:
        if mtime < not_before:
            break
        if _cwd_of(path) in wanted:
            return path
    return None


def _text(content: object) -> str:
    if not isinstance(content, list):
        return ""
    return "\n".join(
        part["text"]
        for part in content
        if isinstance(part, Mapping) and isinstance(part.get("text"), str)
    )


def _exit_ok(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | str):
        return True
    try:
        return int(value) == 0
    except ValueError:
        return True


def _shell_line(command: object) -> str:
    """The command a person would recognise: the last word of ``bash -lc "..."``."""
    if isinstance(command, list) and command:
        return _one_line(str(command[-1] if len(command) >= 3 else " ".join(map(str, command))))
    return _one_line(str(command or ""))


def items_for(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The chat items one rollout line stands for (none for bookkeeping)."""
    if entry.get("type") != "event_msg":
        return []
    payload = entry.get("payload")
    if not isinstance(payload, Mapping) or payload.get("type") != "item_completed":
        return []
    item = payload.get("item")
    if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
        return []
    at = entry.get("timestamp") if isinstance(entry.get("timestamp"), str) else None
    ident = item["id"]
    kind = item.get("type")
    if kind == "UserMessage":
        return [
            {
                "id": ident,
                "kind": "user",
                "at": at,
                "text": _clip(_text(item.get("content")), 20000),
            }
        ]
    if kind == "AgentMessage":
        text = _text(item.get("content"))
        return (
            [{"id": ident, "kind": "assistant", "at": at, "text": _clip(text, 20000)}]
            if text.strip()
            else []
        )
    if kind == "ContextCompaction":
        return [{"id": ident, "kind": "divider", "at": at, "text": "Context was compacted"}]
    if kind == "CommandExecution":
        output = (
            item.get("aggregated_output")
            or item.get("formatted_output")
            or item.get("stdout")
            or ""
        )
        ok = _exit_ok(item.get("exit_code")) and item.get("status") != "failed"
        return [
            {"id": ident, "kind": "tool", "at": at, "tool_id": ident, "name": "Bash",
             "summary": _shell_line(item.get("command"))},
            {"id": f"{ident}#r", "kind": "tool_result", "at": at, "tool_id": ident,
             "text": _clip(str(output), _OUTPUT_CHARS), "is_error": not ok},
        ]  # fmt: skip
    if kind == "FileChange":
        changes = item.get("changes")
        paths = list(changes) if isinstance(changes, Mapping) else []
        added = [
            p
            for p, c in (changes or {}).items()
            if isinstance(c, Mapping) and c.get("type") == "add"
        ]
        out: list[dict[str, Any]] = []
        for number, path in enumerate(paths):
            name = "Write" if path in added else "Edit"
            tool_id = f"{ident}:{number}"
            out.append({"id": tool_id, "kind": "tool", "at": at, "tool_id": tool_id, "name": name,
                        "summary": _one_line(str(path))})  # fmt: skip
            if number == 0:
                out.append({"id": f"{ident}#r", "kind": "tool_result", "at": at, "tool_id": tool_id,
                            "text": _clip(str(item.get("stdout") or ""), _OUTPUT_CHARS),
                            "is_error": item.get("status") == "failed"})  # fmt: skip
        return out
    if kind == "Extension" and isinstance(item.get("kind"), str):
        query = item.get("query")
        return [{"id": ident, "kind": "tool", "at": at, "tool_id": ident, "name": "WebSearch",
                 "summary": _one_line(str(query or item["kind"]))}]  # fmt: skip
    return []


def read_rollout(path: Path, after: int | None, *, scrub: Any = lambda text: text) -> Any:
    """Incremental read of a rollout, as :func:`~cuttlefish.sessions.transcript.read_thread`."""
    return read_thread(path, after, scrub=scrub, parse=items_for, ignored=_IGNORED)
