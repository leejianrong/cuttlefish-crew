"""The worker profile: the tools a machine needs before a project is placed on it (CUT-212).

It is data, `profile.toml` beside this file (or the file `CUTTLEFISH_WORKER_PROFILE` names), so a
person can read it, the bootstrap (CUT-213) can install from it and the controller can compare it
with what a worker reports (CUT-214). `load` validates it and raises `ProfileError` naming the
entry that is wrong; nothing here runs a program.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
_VERSION = re.compile(r"^\d+(\.\d+){0,3}$")
_PACKAGE = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,60}$")
_ARG = re.compile(r"^[A-Za-z0-9-][A-Za-z0-9._-]{0,30}$")
_HOW = frozenset({"uv", "node", "agent-cli"})
_KEYS = frozenset({"name", "command", "version", "min", "group", "apt", "how"})
DEFAULT = Path(__file__).with_name("profile.toml")


class ProfileError(ValueError):
    """The profile file is not valid; the message names the entry."""


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    command: str
    version_args: tuple[str, ...] = ("--version",)
    minimum: str | None = None
    group: str | None = None
    apt: tuple[str, ...] = ()
    how: str | None = None


@dataclass(frozen=True, slots=True)
class Profile:
    tools: tuple[Tool, ...]

    def groups(self) -> dict[str, tuple[Tool, ...]]:
        found: dict[str, list[Tool]] = {}
        for tool in self.tools:
            if tool.group:
                found.setdefault(tool.group, []).append(tool)
        return {name: tuple(members) for name, members in found.items()}


def parse_version(text: str) -> tuple[int, ...] | None:
    """The first dotted number in `text` ("git version 2.43.0" gives (2, 43, 0)), or `None`."""
    found = re.search(r"\d+(?:\.\d+)*", text)
    return tuple(int(part) for part in found.group().split(".")) if found else None


def parse(text: str) -> Profile:
    try:
        body = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"the profile is not valid TOML: {exc}") from exc
    if body.get("version") != 1:
        raise ProfileError("the profile needs `version = 1` at the top")
    raw = body.get("tool")
    if not isinstance(raw, list) or not raw:
        raise ProfileError("the profile lists no [[tool]]")
    tools = tuple(_tool(entry, index) for index, entry in enumerate(raw, 1))
    names = [tool.name for tool in tools]
    for name in names:
        if names.count(name) > 1:
            raise ProfileError(f"tool {name!r} is listed twice")
    return Profile(tools)


def _tool(entry: object, index: int) -> Tool:
    if not isinstance(entry, dict):
        raise ProfileError(f"tool #{index} is not a table")
    name = entry.get("name")
    where = f"tool {name!r}" if isinstance(name, str) else f"tool #{index}"
    if not isinstance(name, str) or not _NAME.match(name):
        raise ProfileError(f"{where}: `name` must be lowercase letters, digits, . _ -")
    if unknown := sorted(set(entry) - _KEYS):
        raise ProfileError(f"{where}: unknown key {unknown[0]!r}")
    command = entry.get("command", name)
    if not isinstance(command, str) or not _NAME.match(command):
        raise ProfileError(f"{where}: `command` must be a plain program name, not a path")
    args = entry.get("version", ["--version"])
    if not (
        isinstance(args, list) and args and all(isinstance(a, str) and _ARG.match(a) for a in args)
    ):
        raise ProfileError(f"{where}: `version` must be a list of plain arguments")
    minimum = entry.get("min")
    if minimum is not None and not (isinstance(minimum, str) and _VERSION.match(minimum)):
        raise ProfileError(f'{where}: `min` must be dotted numbers such as "2.30"')
    group = entry.get("group")
    if group is not None and not (isinstance(group, str) and _NAME.match(group)):
        raise ProfileError(f"{where}: `group` must be a short lowercase word")
    apt = entry.get("apt", [])
    if not (isinstance(apt, list) and all(isinstance(p, str) and _PACKAGE.match(p) for p in apt)):
        raise ProfileError(f"{where}: `apt` must be a list of package names")
    how = entry.get("how")
    if how is not None and how not in _HOW:
        raise ProfileError(f"{where}: `how` must be one of {', '.join(sorted(_HOW))}")
    if bool(apt) == (how is not None):
        raise ProfileError(
            f"{where}: give `apt` or `how` (one way to install it), not both or neither"
        )
    return Tool(name, command, tuple(args), minimum, group, tuple(apt), how)


def load(path: Path | None = None) -> Profile:
    """`path`, else the file `CUTTLEFISH_WORKER_PROFILE` names, else the standard profile."""
    chosen = path or Path(os.environ.get("CUTTLEFISH_WORKER_PROFILE") or DEFAULT)
    try:
        return parse(chosen.read_text())
    except OSError as exc:
        raise ProfileError(f"cannot read the profile {chosen}: {exc}") from exc
