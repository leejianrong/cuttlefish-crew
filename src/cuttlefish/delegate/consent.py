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


def _suggest_words(words: Sequence[str]) -> tuple[str, ...] | None:
    """The leading words of one command up to its first flag or path-like word, at most three."""
    for stop in (_is_flag_or_path, lambda word: word.startswith("-")):
        rule: list[str] = []
        for word in words[:3]:
            if stop(word) or not _WORD.fullmatch(word):
                break
            rule.append(word)
        if rule and not _too_broad(rule):
            return tuple(rule)
    return None


def suggest_rule(line: str) -> tuple[str, ...] | None:
    """The rule "Always allow" proposes for ``line``: its leading words up to the first flag or
    path-like word, at most three (``docker compose up -d postgres`` -> ``docker compose up``).
    ``None`` when the line is not a plain word list or nothing narrow enough can be proposed."""
    words = _plain_words(line)
    if words is None or never_allowed_reason(line) is not None:
        return None
    return _suggest_words(words)


def validate_always_rule(line: str, rule: Sequence[str]) -> tuple[str, ...]:
    """The rule a person may "Always allow" for ``line``, or :class:`ConsentPolicyError`.

    It must be a word-prefix of a command in this very line (a person cannot grant what the
    agent did not ask for): the line itself when it is a plain word list, or one command of a
    chain :func:`split_chain` reads. The entry must be valid as if hand-typed and not so broad
    it would approve any script. Never-allowed always wins afterwards, because the rule is
    applied through :class:`ConsentPolicy`, whose ``decide`` checks that list first.
    """
    words = _plain_words(line)
    commands = [words] if words is not None else split_chain(line)
    if commands is None:
        raise ConsentPolicyError("this command has shell syntax, so it can only be allowed once")
    pattern = validate_allow_entry(rule)
    if not any(tuple(command[: len(pattern)]) == pattern for command in commands):
        raise ConsentPolicyError("the rule must be the start of the command the agent asked to run")
    return pattern


def _escapes_root(word: str) -> bool:
    """Whether an argument reaches outside the working tree: absolute, or with a ``..``
    segment -- in the word itself or in a ``--flag=value`` value."""
    return any(part.startswith("/") or ".." in part.split("/") for part in word.split("="))


#: A redirect that cannot write anywhere that matters: stderr into stdout, or output discarded.
_HARMLESS_REDIRECTS = ("2>&1", "2>/dev/null", ">/dev/null")
#: Builtins that only set an exit status; ``cmd || true`` is the usual way to ignore a failure.
_STATUS_ONLY = frozenset({"true", "false"})
#: Characters ``/bin/sh`` still interprets inside double quotes, so a double-quoted word with one
#: cannot be read as a literal.
_DOUBLE_QUOTE_ACTIVE = frozenset("$`\\!\n")


def split_chain(line: str) -> list[list[str]] | None:
    """The simple commands in a chained command line, each as its literal words, or ``None``.

    Coding agents chain: ``pwd && rg --files -g 'AGENTS.md' && git status``, one command a line,
    ``pytest || true``. Reading that as one opaque string means every such line is asked about
    even when each command in it is on the list. This reads ``&&``, ``||``, ``;``, ``|`` and
    newline as separators and returns the words of each command, so each can be decided alone.

    Fails closed: ``None`` for anything not positively understood -- a redirect other than
    ``2>&1``, ``2>/dev/null`` or ``>/dev/null``, a lone ``&``, ``$``, a backtick, parentheses,
    braces, a glob or ``~`` outside quotes, a backslash, ``!`` or ``$`` inside double quotes, an
    unclosed quote, an empty command, or stray spaces (the same strictness as a plain word list).
    Single-quoted text is literal to the shell, so it may hold anything but a newline.
    """
    commands: list[list[str]] = []
    words: list[str] = []
    word: list[str] = []
    in_word = False
    prev = ""
    i, n = 0, len(line)

    def end_word() -> None:
        nonlocal in_word
        if in_word:
            words.append("".join(word))
            word.clear()
            in_word = False

    def end_command(*, allow_empty: bool = False) -> bool:
        end_word()
        if not words:
            return allow_empty
        commands.append(words.copy())
        words.clear()
        return True

    while i < n:
        ch = line[i]
        if ch == " ":
            if prev in ("", " ", "\n"):
                return None
            end_word()
            prev = ch
            i += 1
            continue
        if ch == "\n":
            if prev in ("", " ") or not end_command(allow_empty=prev == "\n"):
                return None
            prev = ch
            i += 1
            continue
        if ch in ";|&":
            pair = line[i : i + 2]
            if ch == "&" and pair != "&&":
                return None
            op = pair if pair in ("&&", "||") else ch
            if not end_command():
                return None
            i += len(op)
            prev = op[-1]
            # An operator is followed by a space or the next word, never by another operator.
            if i < n and line[i] in ";|&\n":
                return None
            continue
        if not in_word:
            redirect = next((r for r in _HARMLESS_REDIRECTS if line.startswith(r, i)), None)
            if redirect is not None and prev == " " and words:
                tail = line[i + len(redirect) : i + len(redirect) + 1]
                if tail in ("", " ", "\n", ";", "|", "&"):
                    i += len(redirect)
                    prev = redirect[-1]
                    continue
        if ch == "'":
            end = line.find("'", i + 1)
            if end == -1 or "\n" in line[i + 1 : end]:
                return None
            word.append(line[i + 1 : end])
            in_word = True
            i = end + 1
            prev = "'"
            continue
        if ch == '"':
            end = line.find('"', i + 1)
            body = line[i + 1 : end] if end != -1 else ""
            if end == -1 or any(c in _DOUBLE_QUOTE_ACTIVE for c in body):
                return None
            word.append(body)
            in_word = True
            i = end + 1
            prev = '"'
            continue
        if not _WORD.fullmatch(ch):
            return None
        word.append(ch)
        in_word = True
        prev = ch
        i += 1
    if prev in (" ", "|") or (prev == "&" and line.endswith("&")):
        return None
    if not end_command(allow_empty=prev in (";", "\n")):
        return None
    return commands


def _is_lookup(words: Sequence[str]) -> bool:
    """``command -v <name>``: asks the shell where a program is, runs nothing."""
    return len(words) == 3 and words[:2] == ["command", "-v"] and bool(_WORD.fullmatch(words[2]))


def _is_commit_message(words: list[str]) -> bool:
    """``git commit [-a] -m <message>`` or ``git commit -am <message>``, the message one word."""
    if words[:2] != ["git", "commit"]:
        return False
    rest = words[2:]
    if len(rest) == 3 and rest[:2] == ["-a", "-m"]:
        message = rest[2]
    elif len(rest) == 2 and rest[0] in ("-m", "-am"):
        message = rest[1]
    else:
        return False
    return 1 <= len(message) <= 500 and "\n" not in message


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
        if _is_lookup(words):
            return ConsentDecision("allow", "allow:command -v")
        if not all(_WORD.fullmatch(word) for word in words):
            return self._decide_chain(line)
        for pattern in self._patterns:
            if tuple(words[: len(pattern)]) != pattern:
                continue
            if unsafe_flag(words) is not None:
                return _deny("unsafe_flag")
            if any(_escapes_root(word) for word in words[len(pattern) :]):
                return _deny("argument_escapes_root")
            return ConsentDecision("allow", "allow:" + " ".join(pattern))
        return _deny("no_matching_allow_entry", askable=True)

    def suggest_rule(self, line: str) -> tuple[str, ...] | None:
        """The rule "Always allow" proposes for a line this policy denied: for a plain word list
        its own start, for a chain the start of the first command nothing allows (CUT-99), so the
        same chain runs unasked next time. ``None`` when nothing narrow and safe can be named."""
        if never_allowed_reason(line) is not None:
            return None
        if _plain_words(line) is not None:
            return suggest_rule(line)
        commands = split_chain(line)
        for words in commands or ():
            if tuple(words) in {(name,) for name in _STATUS_ONLY}:
                continue
            if never_allowed_reason(" ".join(words)) is not None:
                return None
            if isinstance(self._match_words(words), ConsentDecision):
                return _suggest_words(words)
        return None

    def _decide_chain(self, line: str) -> ConsentDecision:
        """A line that is more than one plain word list: allowed when every command in it is.

        The whole line already passed the never-allowed check; each command is checked again on
        its own words, then matched against the same patterns a plain line uses.
        """
        commands = split_chain(line)
        if commands is None:
            return _deny("not_a_plain_word_list", askable=True)
        matched: list[str] = []
        for words in commands:
            if tuple(words) in {(name,) for name in _STATUS_ONLY}:
                continue
            reason = never_allowed_reason(" ".join(words))
            if reason is not None:
                return _deny(reason)
            rule = self._match_words(words)
            if isinstance(rule, ConsentDecision):
                return rule
            matched.append(rule)
        return ConsentDecision("allow", "allow:" + " | ".join(dict.fromkeys(matched)))

    def _match_words(self, words: list[str]) -> str | ConsentDecision:
        """The rule that allows one command's words, or the denial."""
        if _is_lookup(words):
            return "command -v"
        if self._commit_messages and _is_commit_message(words):
            return "git commit -m"
        for pattern in self._patterns:
            if tuple(words[: len(pattern)]) != pattern:
                continue
            if pattern == _GIT_COMMIT:
                # Inside a chain a bare ``git commit`` entry opens only the message form
                # above: the unquoted words cannot say whether an argument was quoted.
                continue
            if unsafe_flag(words) is not None:
                return _deny("unsafe_flag")
            if any(_escapes_root(word) for word in words[len(pattern) :]):
                return _deny("argument_escapes_root")
            return " ".join(pattern)
        return _deny("no_matching_allow_entry", askable=True)
