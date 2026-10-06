"""The commands no permission mode, allow rule or answer ever approves.

One shared definition, used by every backend and every mode (Ask first, Standard, Auto,
docs/SLICES.md V4): privilege escalation, a forced ``git push``, and a downloaded script
piped into a shell. A write outside the project root is the fourth member of the list; it
is decided where the root is known (``consent.ConsentPolicy`` for ``write_outside_root``
and ``_escapes_root`` for path arguments), not here.

This module reads a command line the way a human would skim it, so it also catches the
chained forms (``ls && sudo x``) that Standard mode already refuses by being a plain word
list. Auto mode will not be a plain word list, which is why the check lives apart from it.
It is a floor, not containment: an approved command still runs as this user.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

#: A command that gains privileges. Never approved, whatever it wraps.
_PRIVILEGE_ESCALATION = frozenset({"sudo", "su", "doas"})

#: A ``git push`` argument that rewrites or deletes remote history. ``+refspec`` is a force.
_FORCE_PUSH_FLAGS = frozenset({"-f", "--force", "--mirror", "--delete", "-d"})

#: Splits a command line into the commands a shell would run: ``;``, ``&``, ``|``, newline,
#: ``$(`` and a backtick all start a new one.
_SEGMENT_SPLIT = re.compile(r"[;&|\n`]|\$\(")

#: ``curl ... | sh`` and its kin: a download whose output is fed to a shell.
_PIPE_TO_SHELL = re.compile(
    r"\b(?:curl|wget|fetch)\b[^|;&\n]*\|\s*(?:sudo\s+)?(?:env\s+)?(?:ba|z|da|k)?sh\b"
)

#: Commands that change or delete the paths they are given. In ``auto`` mode no allow list
#: stands between the model and these, so a path that leaves the project root is refused
#: here. Best effort on a command line read as text, not containment: a script, an alias or a
#: path built at run time can still write elsewhere, and any ``$`` or backtick on the line
#: makes the target unknowable, so a write command on such a line is refused.
_WRITES_EVERY_ARG = frozenset(
    {
        "rm",
        "rmdir",
        "unlink",
        "shred",
        "touch",
        "mkdir",
        "chmod",
        "chown",
        "chgrp",
        "truncate",
        "tee",
    }
)
#: Commands whose last argument is the destination; earlier arguments are only read.
_WRITES_LAST_ARG = frozenset({"cp", "mv", "ln", "install"})

#: ``> target`` and ``>> target`` (``2>&1`` and friends are not file targets).
_REDIRECT = re.compile(r">>?\s*(\S+)")

#: Flags that turn an otherwise ordinary command into one that writes, deletes or runs
#: something else. Keyed by the command's own leading words. Applied to every allow rule,
#: declared or built in, because the preset's ``find`` and ``rg`` are only safe without them.
UNSAFE_FLAGS: Mapping[tuple[str, ...], frozenset[str]] = {
    ("find",): frozenset(
        {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
    ),
    ("rg",): frozenset({"--pre"}),
    ("git", "commit"): frozenset({"--no-verify", "-n"}),
}


def _leaves_root(argument: str) -> bool:
    """Whether a path argument reaches outside the working tree: absolute, home-relative,
    with a ``..`` segment, or built from a variable or substitution."""
    path = argument.strip("'\"")
    return path.startswith(("/", "~")) or ".." in path.split("/") or "$" in path or "`" in path


def _writes_outside_root(words: list[str], *, dynamic: bool) -> bool:
    command = words[0]
    arguments = [word for word in words[1:] if not word.startswith("-")]
    if command in _WRITES_EVERY_ARG:
        return dynamic or any(_leaves_root(argument) for argument in arguments)
    if command in _WRITES_LAST_ARG:
        return bool(arguments) and (dynamic or _leaves_root(arguments[-1]))
    if command == "dd":
        return any(word.startswith("of=") and _leaves_root(word[3:]) for word in words[1:])
    return False


def never_allowed_reason(line: str) -> str | None:
    """Why `line` may never be approved, or ``None`` when no never-allowed rule applies."""
    if _PIPE_TO_SHELL.search(line):
        return "never_allowed:pipe_to_shell"
    for match in _REDIRECT.finditer(line):
        target = match.group(1)
        if not target.startswith("&") and target != "/dev/null" and _leaves_root(target):
            return "never_allowed:write_outside_root"
    dynamic = "$" in line or "`" in line
    for segment in _SEGMENT_SPLIT.split(line):
        words = segment.split()
        if not words:
            continue
        if words[0] in _PRIVILEGE_ESCALATION:
            return "never_allowed:privilege_escalation"
        if words[0] == "git" and "push" in words[1:]:
            args = words[words.index("push", 1) + 1 :]
            if any(arg in _FORCE_PUSH_FLAGS or arg.startswith(("--force", "+")) for arg in args):
                return "never_allowed:force_push"
        if _writes_outside_root(words, dynamic=dynamic):
            return "never_allowed:write_outside_root"
    return None


def unsafe_flag(words: list[str]) -> str | None:
    """The first unsafe flag in `words` (a split command line), or ``None``."""
    for prefix, flags in UNSAFE_FLAGS.items():
        if tuple(words[: len(prefix)]) != prefix:
            continue
        for word in words[len(prefix) :]:
            if word in flags or word.split("=", 1)[0] in flags:
                return word
    return None
