"""The consent policy for ``kopicode serve``'s live ``consent.request`` (kopicode ADR-0016).

kopicode's own ``--policy-file`` allowlist matches the whole ``/bin/sh -c "<line>"`` argv
byte-for-byte, and kopicode has rejected loosening that three times: a prefix match that
allows ``uv run pytest`` also allows ``uv run pytest && rm -rf /``. Here the client owns the
decision, so it can do the one thing kopicode cannot -- match by argv prefix *after*
refusing any command line that isn't a plain, space-separated word list. With no shell
metacharacter, quoting, or expansion left in the line, ``/bin/sh -c`` runs exactly the
words that were matched, so a prefix match is sound.

``detail`` is untrusted model output. Every rule here fails closed: anything not positively
classified is a ``deny``, and the policy never answers ``allow_session`` (every decision is
made, and logged, individually).

This decides which commands cuttlefish *approves*. It is not containment: an approved
``uv run pytest`` still executes arbitrary repository code as this user. Real containment is
this project's job (a sandbox), not kopicode's (its ADR-0008/0011) and not this module's.
"""

from __future__ import annotations

import dataclasses
import re
import shlex
from collections.abc import Sequence
from typing import Literal

Answer = Literal["allow", "deny"]

#: Longest command line ever considered; a longer one is denied unread.
MAX_COMMAND_CHARS = 1024

#: A command line is a run of these words, joined by single spaces -- no quote, `;`, `&`,
#: `|`, `<`, `>`, `$`, backtick, parenthesis, brace, backslash, glob character, `~`, `#`,
#: newline, or tab can appear. An allowlist of characters, not a denylist of metacharacters.
_WORD = re.compile(r"[A-Za-z0-9_.,:=@%+/-]+")

_SH_C_PREFIX = ("/bin/sh", "-c")


@dataclasses.dataclass(frozen=True, slots=True)
class ConsentDecision:
    """One answer to one ``consent.request``, with why -- what gets journaled."""

    answer: Answer
    rule: str


class ConsentPolicyError(ValueError):
    """A declared allow entry this policy cannot express safely (raised at config time)."""


def _deny(rule: str) -> ConsentDecision:
    return ConsentDecision("deny", rule)


def _pattern_from_entry(entry: Sequence[str]) -> tuple[str, ...]:
    """One declared ``allow`` entry as a word-list pattern.

    Two shapes are accepted: an argv (``["uv", "run", "pytest"]`` -- what ``--allow 'uv run
    pytest'`` produces) and kopicode's own ``["/bin/sh", "-c", "uv run pytest"]``. Either
    way the words must be plain; a pattern with a metacharacter in it could never match a
    line this policy lets through, so it is refused up front rather than silently dead.
    """
    words = tuple(entry)
    if words[:2] == _SH_C_PREFIX:
        if len(words) != 3:
            raise ConsentPolicyError(f"allow entry {list(entry)!r}: /bin/sh -c takes one line")
        try:
            words = tuple(shlex.split(words[2]))
        except ValueError as exc:
            raise ConsentPolicyError(f"allow entry {list(entry)!r}: {exc}") from exc
    if not words:
        raise ConsentPolicyError("allow entry is empty")
    for word in words:
        if not _WORD.fullmatch(word):
            raise ConsentPolicyError(
                f"allow entry {list(entry)!r}: word {word!r} is not a plain word "
                f"(letters, digits and _.,:=@%+/- only)"
            )
    return words


def _escapes_root(word: str) -> bool:
    """Whether an argument reaches outside the working tree: absolute, or with a ``..``
    segment -- in the word itself or in a ``--flag=value`` value."""
    return any(part.startswith("/") or ".." in part.split("/") for part in word.split("="))


class ConsentPolicy:
    """A role's consent policy, built from the role's declared ``allow`` list.

    The default -- no ``allow`` -- denies every shell command, which is exactly what a
    read-only role should get; ``write_outside_root`` is denied for every role, always.
    """

    def __init__(self, allow: Sequence[Sequence[str]] | None = None) -> None:
        self._patterns = tuple(_pattern_from_entry(entry) for entry in allow or ())

    def decide(self, kind: str, detail: str) -> ConsentDecision:
        if kind == "write_outside_root":
            return _deny("write_outside_root_never")
        if kind != "run_shell":
            return _deny("unknown_kind")
        if not self._patterns:
            return _deny("no_shell_allowed")
        if not detail or len(detail) > MAX_COMMAND_CHARS:
            return _deny("command_length")
        words = detail.split(" ")
        if not all(_WORD.fullmatch(word) for word in words):
            return _deny("not_a_plain_word_list")
        for pattern in self._patterns:
            if tuple(words[: len(pattern)]) != pattern:
                continue
            if any(_escapes_root(word) for word in words[len(pattern) :]):
                return _deny("argument_escapes_root")
            return ConsentDecision("allow", "allow:" + " ".join(pattern))
        return _deny("no_matching_allow_entry")
