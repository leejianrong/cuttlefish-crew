"""Built-in allow presets: the shell commands an agent may run without asking.

A project that declares nothing used to get no shell command at all, so a freshly
registered team could edit files but not run its own tests (operator feedback,
2026-10-06, docs/SLICES.md V4-A). ``resolve_allow`` is the one place the default is
applied: the built-in presets, then whatever the operator declared on top. It runs only inside
the delegation task, never in a task argument or the journaled declaration, so replay
arguments do not change.

Every entry is a plain argv prefix in ``consent.ConsentPolicy``'s grammar. Prefix matching
means ``uv run pytest`` also covers ``uv run pytest -q tests/``; arguments that reach outside
the root, and the flags in ``never_allowed.UNSAFE_FLAGS``, are refused whatever the prefix.
Commands that execute repository code (a test run, a build) are here on purpose: approving
them is a decision, not containment, the same trust model as ADR-0002.

``git commit`` is listed bare, which only switches on ``ConsentPolicy``'s own quote-aware
``git commit -m '<message>'`` rule; the plain word-list match could never express a message.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

Command = tuple[str, ...]

PRESETS: Mapping[str, tuple[Command, ...]] = {
    "inspect": (
        ("ls",),
        ("cat",),
        ("head",),
        ("tail",),
        ("wc",),
        ("grep",),
        ("rg",),
        ("find",),
        ("pwd",),
        ("stat",),
        ("tree",),
        ("diff",),
    ),
    "git-read": (
        ("git", "status"),
        ("git", "diff"),
        ("git", "log"),
        ("git", "show"),
        ("git", "ls-files"),
        ("git", "rev-parse"),
        ("git", "blame"),
        ("git", "branch", "--show-current"),
        ("git", "branch", "--list"),
        ("git", "branch", "-a"),
        ("git", "branch", "-vv"),
    ),
    "git-save": (
        ("git", "add"),
        ("git", "commit"),
        ("git", "switch", "-c"),
        ("git", "checkout", "-b"),
    ),
    "python": (
        ("uv", "run", "pytest"),
        ("uv", "run", "ruff"),
        ("uv", "run", "mypy"),
        ("uv", "sync"),
        ("python", "-m", "pytest"),
        ("python3", "-m", "pytest"),
    ),
    "javascript": (
        ("npm", "test"),
        ("npm", "run", "test"),
        ("npm", "run", "build"),
        ("npm", "run", "lint"),
        ("npm", "run", "check"),
        ("npm", "ci"),
        ("npx", "tsc"),
    ),
    "make": (
        ("make", "test"),
        ("make", "check"),
        ("make", "lint"),
        ("make", "build"),
        ("make", "ci"),
    ),
    # Opt in: off unless a project asks for them.
    "go-rust": (
        ("go", "test"),
        ("go", "build"),
        ("go", "vet"),
        ("cargo", "test"),
        ("cargo", "build"),
        ("cargo", "check"),
        ("cargo", "clippy"),
    ),
    "containers": (
        ("docker", "compose", "up"),
        ("docker", "compose", "ps"),
        ("docker", "compose", "logs"),
    ),
}

#: What a read-only role may run: look, never change. Declared commands do not widen it.
READ_ONLY_PRESETS: tuple[str, ...] = ("inspect", "git-read")

#: What a call that declares nothing gets.
DEFAULT_PRESETS: tuple[str, ...] = (
    "inspect",
    "git-read",
    "git-save",
    "python",
    "javascript",
    "make",
)


def preset_allow(names: Sequence[str]) -> list[list[str]]:
    """The commands of the named presets, in order, as the argv lists backends take."""
    commands: list[list[str]] = []
    for name in names:
        commands.extend(list(command) for command in PRESETS[name])
    return commands


def read_only_allow() -> list[list[str]]:
    """The shell a read-only role gets (``READ_ONLY_PRESETS``), whatever else is declared."""
    return preset_allow(READ_ONLY_PRESETS)


def resolve_allow(declared: Sequence[Sequence[str]] | None = None) -> list[list[str]]:
    """The effective allow list: the default presets, then `declared` on top.

    Additive and order-preserving, with duplicates dropped, so passing a list that
    already includes the presets is a no-op. There is no "declare nothing at all" form
    here yet; a read-only role is a later slice (V4-B/V4-C), not a way to pass ``[]``.
    """
    effective: list[list[str]] = []
    seen: set[Command] = set()
    for entry in (*preset_allow(DEFAULT_PRESETS), *(declared or ())):
        key = tuple(entry)
        if key not in seen:
            seen.add(key)
            effective.append(list(key))
    return effective
