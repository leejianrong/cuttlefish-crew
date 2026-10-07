"""The consent policy for ``kopicode serve``'s live ``consent.request`` (kopicode ADR-0016).

kopicode's own ``--policy-file`` allowlist matches the whole ``/bin/sh -c "<line>"`` argv
byte-for-byte, and kopicode has rejected loosening that three times: a prefix match that
allows ``uv run pytest`` also allows ``uv run pytest && rm -rf /``. Here the client owns the
decision, so it can do the one thing kopicode cannot -- match by argv prefix *after*
refusing any command line that isn't a plain, space-separated word list. With no shell
metacharacter, quoting, or expansion left in the line, ``/bin/sh -c`` runs exactly the
words that were matched, so a prefix match is sound.

``detail`` is untrusted model output; for a shell command it is ``/bin/sh -c <line>``. Every
rule here fails closed: anything not positively classified is a ``deny``, and the policy never
answers ``allow_session`` (every decision is made, and logged, individually).

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

from cuttlefish.delegate.never_allowed import never_allowed_reason, unsafe_flag

Answer = Literal["allow", "deny"]

#: Longest command line ever considered; a longer one is denied unread.
MAX_COMMAND_CHARS = 1024

#: A command line is a run of these words, joined by single spaces -- no quote, `;`, `&`,
#: `|`, `<`, `>`, `$`, backtick, parenthesis, brace, backslash, glob character, `~`, `#`,
#: newline, or tab can appear. An allowlist of characters, not a denylist of metacharacters.
_WORD = re.compile(r"[A-Za-z0-9_.,:=@%+/-]+")

_SH_C_PREFIX = ("/bin/sh", "-c")

#: The one command whose argument is prose: ``git commit -m '<message>'`` (or ``-am``). A
#: message needs quotes, which the plain-word rule above refuses, so it gets its own narrow
#: match. Single quotes hold anything but a quote or newline; double quotes additionally
#: exclude every character ``/bin/sh`` would still expand inside them (``$``, backtick,
#: backslash, ``!``). No other flag is accepted, so nothing else rides along.
_GIT_COMMIT_MESSAGE = re.compile(
    r"git commit (?:-a )?-m (?:'[^'\n]{1,500}'|\"[^\"$`\\!\n]{1,500}\")"
    r"|git commit -am (?:'[^'\n]{1,500}'|\"[^\"$`\\!\n]{1,500}\")"
)
_GIT_COMMIT = ("git", "commit")

#: What kopicode puts in a ``run_shell`` ``consent.request``'s ``detail``: the argv it will run,
#: ``["/bin/sh", "-c", <line>]``, joined by spaces (``internal/permission/gate.go``), so the
#: line follows this prefix. Found live (KAN-1794): kopicode's protocol doc shows the bare line,
#: and a policy written to that denied every real command. A detail without the prefix is not
#: what kopicode sends, so it is denied rather than guessed at.
_DETAIL_PREFIX = " ".join(_SH_C_PREFIX) + " "


@dataclasses.dataclass(frozen=True, slots=True)
class ConsentDecision:
    """One answer to one ``consent.request``, with why -- what gets journaled.

    ``askable`` marks a denial a person could override (ADR-0028): the command is merely not
    approved by any rule. A never-allowed command, an unsafe flag or a path escape is never
    askable, because no answer could change it.
    """

    answer: Answer
    rule: str
    askable: bool = False
    #: A person answered (ADR-0028): journaled as the request pair, not as ``ConsentDecided``.
    asked: bool = False


class ConsentPolicyError(ValueError):
    """A declared allow entry this policy cannot express safely (raised at config time)."""


def _deny(rule: str, *, askable: bool = False) -> ConsentDecision:
    return ConsentDecision("deny", rule, askable)


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
    if (reason := never_allowed_reason(" ".join(words))) is not None:
        raise ConsentPolicyError(f"allow entry {list(entry)!r} is never allowed ({reason})")
    for word in words:
        if not _WORD.fullmatch(word):
            raise ConsentPolicyError(
                f"allow entry {list(entry)!r}: word {word!r} is not a plain word "
                f"(letters, digits and _.,:=@%+/- only)"
            )
    return words


def command_line(detail: str) -> str | None:
    """The shell line in a ``run_shell`` ``consent.request`` ``detail``, or ``None`` when the
    detail is not ``/bin/sh -c <line>`` or the line is empty or longer than
    :data:`MAX_COMMAND_CHARS`."""
    if not detail.startswith(_DETAIL_PREFIX):
        return None
    line = detail[len(_DETAIL_PREFIX) :]
    if not line or len(line) > MAX_COMMAND_CHARS:
        return None
    return line


def validate_allow_entry(entry: Sequence[str]) -> tuple[str, ...]:
    """One allow entry as plain words, or :class:`ConsentPolicyError` saying why not.

    The one check every route into a project's commands shares: a hand-typed entry and an
    "Always allow" answer (ADR-0028). Plain words only, and nothing never-allowed.
    """
    pattern = _pattern_from_entry(entry)
    if _too_broad(pattern):
        raise ConsentPolicyError(
            f"{' '.join(pattern)!r} is too broad: it would allow any script to run. "
            "Name what it may run, for example 'python -m pytest'"
        )
    return pattern


#: Programs that run whatever they are handed. A rule naming only one of these would approve
#: any script (ADR-0021's ``["python"]`` warning), so "Always allow" refuses it.
_LAUNCHERS = frozenset(
    {
        "sh", "bash", "zsh", "dash", "ksh", "fish", "env", "xargs", "eval", "exec", "source",
        "command", "builtin", "time", "nohup", "nice", "timeout", "watch", "python", "python3",
        "node", "nodejs", "deno", "bun", "ruby", "perl", "php", "lua", "uv", "pipx", "npx", "bunx",
    }
)  # fmt: skip
_CODE_FLAGS = frozenset({"-c", "-e", "--eval", "--command", "-exec"})


def _too_broad(words: Sequence[str]) -> bool:
    return words[0] in _LAUNCHERS and (len(words) == 1 or words[-1] in _CODE_FLAGS)


def _plain_words(line: str) -> list[str] | None:
    words = line.split(" ")
    return words if all(_WORD.fullmatch(word) for word in words) else None


def _is_flag_or_path(word: str) -> bool:
    return word.startswith(("-", ".")) or "/" in word or "=" in word


def suggest_rule(line: str) -> tuple[str, ...] | None:
    """The rule "Always allow" proposes for ``line``: its leading words up to the first flag or
    path-like word, at most three (``docker compose up -d postgres`` -> ``docker compose up``).
    ``None`` when the line is not a plain word list or nothing narrow enough can be proposed."""
    words = _plain_words(line)
    if words is None or never_allowed_reason(line) is not None:
        return None
    for stop in (_is_flag_or_path, lambda word: word.startswith("-")):
        rule: list[str] = []
        for word in words[:3]:
            if stop(word):
                break
            rule.append(word)
        if rule and not _too_broad(rule):
            return tuple(rule)
    return None


def validate_always_rule(line: str, rule: Sequence[str]) -> tuple[str, ...]:
    """The rule a person may "Always allow" for ``line``, or :class:`ConsentPolicyError`.

    It must be a word-prefix of this very line (a person cannot grant what the agent did not
    ask for), the line a plain word list, the entry valid as if hand-typed, and not so broad it
    would approve any script. Never-allowed always wins afterwards, because the rule is applied
    through :class:`ConsentPolicy`, whose ``decide`` checks that list first.
    """
    words = _plain_words(line)
    if words is None:
        raise ConsentPolicyError("this command has shell syntax, so it can only be allowed once")
    pattern = validate_allow_entry(rule)
    if tuple(words[: len(pattern)]) != pattern:
        raise ConsentPolicyError("the rule must be the start of the command the agent asked to run")
    return pattern


def _escapes_root(word: str) -> bool:
    """Whether an argument reaches outside the working tree: absolute, or with a ``..``
    segment -- in the word itself or in a ``--flag=value`` value."""
    return any(part.startswith("/") or ".." in part.split("/") for part in word.split("="))


class ConsentPolicy:
    """A role's consent policy, built from the role's declared ``allow`` list.

    ``auto=True`` (permission mode Auto, V4-C) answers ``allow`` to every shell command that
    is not never-allowed, whatever the list says; the plain-word-list rule does not apply,
    so a chained or quoted command line is fine there. ``write_outside_root`` stays denied.

    The default -- no ``allow`` -- denies every shell command, which is exactly what a
    read-only role should get; ``write_outside_root`` is denied for every role, always.
    """

    def __init__(self, allow: Sequence[Sequence[str]] | None = None, *, auto: bool = False) -> None:
        self._auto = auto
        self._patterns = tuple(_pattern_from_entry(entry) for entry in allow or ())
        self._commit_messages = _GIT_COMMIT in self._patterns

    def decide(self, kind: str, detail: str) -> ConsentDecision:
        if kind == "write_outside_root":
            return _deny("write_outside_root_never")
        if kind != "run_shell":
            return _deny("unknown_kind")
        line = command_line(detail)
        if not self._auto and not self._patterns:
            # Nothing is allowed, so a person may be asked -- but only about a line that no
            # never-allowed rule refuses (ADR-0028).
            if line is None or never_allowed_reason(line) is not None:
                return _deny("no_shell_allowed")
            return _deny("no_shell_allowed", askable=True)
        if line is None:
            return _deny(
                "not_a_sh_c_command" if not detail.startswith(_DETAIL_PREFIX) else "command_length"
            )
        reason = never_allowed_reason(line)
        if reason is not None:
            return _deny(reason)
        if self._auto:
            return ConsentDecision("allow", "auto")
        if self._commit_messages and _GIT_COMMIT_MESSAGE.fullmatch(line):
            return ConsentDecision("allow", "allow:git commit -m")
        words = line.split(" ")
        if not all(_WORD.fullmatch(word) for word in words):
            return _deny("not_a_plain_word_list", askable=True)
        for pattern in self._patterns:
            if tuple(words[: len(pattern)]) != pattern:
                continue
            if unsafe_flag(words) is not None:
                return _deny("unsafe_flag")
            if any(_escapes_root(word) for word in words[len(pattern) :]):
                return _deny("argument_escapes_root")
            return ConsentDecision("allow", "allow:" + " ".join(pattern))
        return _deny("no_matching_allow_entry", askable=True)
