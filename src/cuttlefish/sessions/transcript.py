"""A chat thread read from Claude Code's own session record (CUT-172, ADR-0044).

Claude Code writes every session to ``~/.claude/projects/<cwd with non-alphanumerics as ->/
<sessionId>.jsonl``: one JSON object per line, ``user``, ``assistant`` and ``system`` entries among
a lot of bookkeeping (api requests, file history, titles). The dashboard's chat is this file,
read and turned into a few kinds of item, never stored a second time (ADR-0004). The format is
Claude Code's and not documented as stable (observed on 2.1.300 to 2.1.308), so the parser
ignores what it does not know and says so, rather than failing the whole thread.

Each assistant content block is its own line, sharing a message id; a ``tool_result`` arrives
later as a ``user`` line. Items are therefore small and keyed, and the client attaches a result to
its call by ``tool_id``.

Reading is incremental: ``read_thread`` takes a byte offset and returns the new offset, so a poll
costs what was appended. A first read of a large file starts near its end.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

#: A first read of a bigger file starts this far from its end (aligned to a line).
WINDOW_BYTES = 1_000_000
#: The most one poll returns, so a long backlog arrives over a few polls.
MAX_READ_BYTES = 2_000_000
#: A first read widens its window until it holds this many items, up to the cap below.
WANTED_ITEMS = 120
MAX_WINDOW_BYTES = 32_000_000
#: What one tool call or result keeps for display.
_SUMMARY_CHARS = 240
_RESULT_CHARS = 2000
_TEXT_CHARS = 20000

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")
_COMMAND = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_WRAPPED = ("<local-command-caveat>", "<local-command-stdout>", "<system-reminder>")


def project_dir_name(cwd: str) -> str:
    """How Claude Code names a working directory's folder under ``~/.claude/projects``."""
    return _NON_ALNUM.sub("-", cwd)


def candidate_paths(root: str, session_id: str, home: Path | None = None) -> list[Path]:
    """Where the transcript of ``session_id`` should be, for the project folder ``root``."""
    base = (home if home is not None else Path.home()) / ".claude" / "projects"
    names: list[str] = []
    for cwd in (str(Path(root).resolve()), root):
        name = project_dir_name(cwd)
        if name not in names:
            names.append(name)
    return [base / name / f"{session_id}.jsonl" for name in names]


@dataclasses.dataclass
class Thread:
    items: list[dict[str, Any]]
    #: The byte offset to ask for next time.
    cursor: int
    #: The file was larger than a first read takes, so earlier messages are not here.
    earlier: bool = False
    #: The file got shorter than the cursor: the client should drop what it has.
    reset: bool = False
    #: Lines that were not understood, so a format change is visible and not silent.
    skipped: int = 0


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _one_line(text: str) -> str:
    return _clip(" ".join(text.split()), _SUMMARY_CHARS)


def summarise_call(name: str, tool_input: Mapping[str, Any]) -> str:
    """One line for a tool call: the command for a shell, the path for a file tool, else the
    first plain value."""
    for key in (
        "command",
        "file_path",
        "notebook_path",
        "pattern",
        "path",
        "url",
        "query",
        "prompt",
    ):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return _one_line(value)
    for value in tool_input.values():
        if isinstance(value, str) and value.strip():
            return _one_line(value)
    return ""


def _result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b["text"]
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        ]
        return "\n".join(parts)
    return ""


def _item(entry: Mapping[str, Any], kind: str, suffix: str = "", **fields: Any) -> dict[str, Any]:
    uuid = entry.get("uuid")
    return {
        "id": f"{uuid}{suffix}" if isinstance(uuid, str) else None,
        "kind": kind,
        "at": entry.get("timestamp") if isinstance(entry.get("timestamp"), str) else None,
        **fields,
    }


def items_for(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The chat items one transcript line stands for (none for bookkeeping)."""
    kind = entry.get("type")
    if entry.get("isSidechain") is True:
        return []  # a sub-agent's own conversation, not this thread
    message = entry.get("message")
    if kind == "user" and isinstance(message, Mapping):
        if entry.get("isMeta") is True:
            return []
        if entry.get("isCompactSummary") is True:
            return [_item(entry, "divider", text="Context was compacted")]
        content = message.get("content")
        if isinstance(content, str):
            command = _COMMAND.search(content)
            if command:
                args = _COMMAND_ARGS.search(content)
                line = (command.group(1) + " " + (args.group(1) if args else "")).strip()
                return [_item(entry, "command", text=_one_line(line))]
            if content.lstrip().startswith(_WRAPPED):
                return []
            return [_item(entry, "user", text=_clip(content, _TEXT_CHARS))]
        if isinstance(content, list):
            out: list[dict[str, Any]] = []
            for index, block in enumerate(content):
                if not isinstance(block, Mapping):
                    continue
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    text = block["text"]
                    if not text.lstrip().startswith(_WRAPPED):
                        out.append(_item(entry, "user", f"#{index}", text=_clip(text, _TEXT_CHARS)))
                elif block.get("type") == "tool_result" and isinstance(
                    block.get("tool_use_id"), str
                ):
                    out.append(
                        _item(
                            entry,
                            "tool_result",
                            f"#{index}",
                            tool_id=block["tool_use_id"],
                            text=_clip(_result_text(block.get("content")), _RESULT_CHARS),
                            is_error=block.get("is_error") is True,
                        )
                    )
            return out
    if kind == "assistant" and isinstance(message, Mapping):
        content = message.get("content")
        out = []
        for index, block in enumerate(content if isinstance(content, list) else []):
            if not isinstance(block, Mapping):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                if block["text"].strip():
                    out.append(
                        _item(
                            entry,
                            "assistant",
                            f"#{index}",
                            text=_clip(block["text"], _TEXT_CHARS),
                        )
                    )
            elif block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                raw = block.get("input")
                name = str(block.get("name") or "tool")
                out.append(
                    _item(
                        entry,
                        "tool",
                        f"#{index}",
                        tool_id=block["id"],
                        name=name,
                        summary=summarise_call(name, raw if isinstance(raw, Mapping) else {}),
                    )
                )
        return out
    if kind == "system":
        subtype = entry.get("subtype")
        content = entry.get("content") if isinstance(entry.get("content"), str) else ""
        if subtype == "compact_boundary":
            return [_item(entry, "divider", text="Context was compacted")]
        if subtype == "away_summary" and content:
            return [_item(entry, "note", text=_clip(content, _TEXT_CHARS))]
    if kind == "continued-in":
        return [_item(entry, "divider", text="Continued in a new session")]
    return []


#: Entry types that are bookkeeping, so a line of one is never "not understood".
_KNOWN_IGNORED = frozenset(
    {
        "attachment", "api-request", "api-request-blob", "api-request-shape", "last-prompt", "mode",
        "atis-latch", "ai-title", "pr-link", "file-history-snapshot", "file-history-delta",
        "queue-operation", "cost-state", "summary", "custom-title", "agent-name",
        "permission-mode", "worktree-state",
    }
)  # fmt: skip


def _parse(data: bytes, scrub: Callable[[str], str]) -> tuple[list[dict[str, Any]], int]:
    """The items in whole lines of ``data``, and how many lines were not understood."""
    items: list[dict[str, Any]] = []
    skipped = 0
    for raw in data.splitlines():
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(entry, dict):
            skipped += 1
            continue
        if entry.get("type") not in _KNOWN_IGNORED:
            for found in items_for(entry):
                items.append(json.loads(scrub(json.dumps(found))))
    return items, skipped


def _whole_lines(data: bytes, *, from_middle: bool) -> tuple[bytes, int]:
    """``data`` cut to whole lines: without a partial first line when it began mid-file, and
    without a partial last line. Returns the lines and how many leading bytes were dropped."""
    lead = 0
    if from_middle:
        cut = data.find(b"\n")
        if cut < 0:
            return b"", len(data)
        lead = cut + 1
        data = data[lead:]
    end = data.rfind(b"\n")
    return (data[: end + 1] if end >= 0 else b""), lead


def read_thread(
    path: Path,
    after: int | None = None,
    *,
    scrub: Callable[[str], str] = lambda text: text,
) -> Thread:
    """The items appended to ``path`` since byte offset ``after`` (``None`` for a first read,
    which starts near the end of a large file). Only whole lines are consumed, so a line still
    being written is picked up next time. ``scrub`` is run over each encoded item."""
    size = path.stat().st_size
    reset = after is not None and after > size
    if after is not None and not reset:
        with path.open("rb") as handle:
            handle.seek(after)
            data = handle.read(MAX_READ_BYTES)
        lines, _ = _whole_lines(data, from_middle=False)
        items, skipped = _parse(lines, scrub)
        return Thread(items, after + len(lines), skipped=skipped)
    # A first read (or a restart after the file shrank). Most of a long transcript is bookkeeping
    # (a 70 MB file can hold a few dozen messages in its last megabyte), so the window grows until
    # it holds enough of a thread or is the whole file.
    window = WINDOW_BYTES
    while True:
        start = max(0, size - window)
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read()
        lines, lead = _whole_lines(data, from_middle=start > 0)
        items, skipped = _parse(lines, scrub)
        if len(items) >= WANTED_ITEMS or start == 0 or window >= MAX_WINDOW_BYTES:
            return Thread(items, start + lead + len(lines), start > 0, reset, skipped)
        window *= 4
