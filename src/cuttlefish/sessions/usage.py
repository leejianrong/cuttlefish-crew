"""Tokens a Claude Code session spent per prompt, read from its own transcript (CUT-273, ADR-0055).

Claude Code writes the usage of every model request into the session's JSONL
(``message.usage``). A *turn* is one typed prompt and every request that followed it, tool calls
included. Each content block of one reply is its own line sharing a message id and repeating the
usage, so a message is counted once. cuttlefish reads this and makes no model call. The format is
Claude Code's and not documented as stable: a line this does not understand is skipped.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


@dataclasses.dataclass
class TurnUsage:
    prompt: str
    requests: int = 0
    input: int = 0
    output: int = 0
    cache_create: int = 0
    cache_read: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_create + self.cache_read


def _is_prompt(entry: dict[str, Any]) -> str | None:
    message = entry.get("message")
    if entry.get("type") != "user" or not isinstance(message, dict) or entry.get("isMeta"):
        return None
    content = message.get("content")
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
        # A list holding only tool results is the agent's own loop, not a prompt.
    if isinstance(content, str) and content.strip() and "<command-name>" not in content:
        return content.strip()
    return None


def read_turns(lines: Iterable[str]) -> list[TurnUsage]:
    """The per-prompt usage in a transcript's lines, in order."""
    turns: list[TurnUsage] = []
    seen: set[str] = set()
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        prompt = _is_prompt(entry)
        if prompt is not None:
            turns.append(TurnUsage(prompt=prompt[:80]))
            continue
        message = entry.get("message")
        if entry.get("type") != "assistant" or not isinstance(message, dict) or not turns:
            continue
        usage = message.get("usage")
        mid = message.get("id")
        if not isinstance(usage, dict) or not isinstance(mid, str) or mid in seen:
            continue
        seen.add(mid)
        turn = turns[-1]
        turn.requests += 1
        values = [usage.get(name) for name in _FIELDS]
        a, b, c, d = (v if isinstance(v, int) else 0 for v in values)
        turn.input, turn.output, turn.cache_create, turn.cache_read = (
            turn.input + a, turn.output + b, turn.cache_create + c, turn.cache_read + d,
        )  # fmt: skip
    return turns


def read_file(path: Path) -> list[TurnUsage]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return read_turns(handle)
