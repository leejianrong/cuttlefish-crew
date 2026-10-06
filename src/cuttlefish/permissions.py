"""Permission modes and per-role access (docs/SLICES.md V4-C, ADR-0025).

A project has one **mode**; a role may override it with an **access** level. The effective
access of a delegation is the role's own, else the project's, else ``standard``.

- ``ask-first``: files can be read and edited; no shell command runs on its own. Until live
  prompts exist (V4-H) a command that would need an answer is refused.
- ``standard``: the built-in dev presets plus anything declared (ADR-0023).
- ``auto``: any shell command runs, except the never-allowed list (``never_allowed``).
- ``read-only`` (a role access, not a project mode): inspection-only shell, and no file
  edits where the backend can stop them.

Each backend maps these onto its own controls and says where it cannot; see ADR-0025 and
``agent_docs/known-gaps.md``.
"""

from __future__ import annotations

MODES: tuple[str, ...] = ("ask-first", "standard", "auto")
DEFAULT_MODE = "standard"
READ_ONLY = "read-only"
#: What a role's ``access`` may be: a mode (an explicit override) or read-only. ``None`` on a
#: role means "inherit the project's mode".
ACCESS_LEVELS: tuple[str, ...] = (*MODES, READ_ONLY)


def effective_access(project_mode: str | None, role_access: str | None) -> str:
    """The access a delegation runs with: the role's, else the project's, else standard."""
    return role_access or project_mode or DEFAULT_MODE


#: What the Permissions tab shows for each mode: a title and what it actually does today.
MODE_INFO: dict[str, tuple[str, str]] = {
    "ask-first": (
        "Ask first",
        "Agents can read and edit files. No command runs on its own. Nothing asks you yet, "
        "so a command that would need an answer is refused.",
    ),
    "standard": (
        "Standard",
        "Everyday dev commands run on their own. Anything off the list is refused.",
    ),
    "auto": (
        "Auto",
        "Any command runs without asking, except the always-blocked list.",
    ),
}

#: What the dashboard says about each backend's handling of a mode, kept beside the mapping in
#: ADR-0025 so the two are edited together. Honest about what each cannot do.
BACKEND_NOTES: tuple[tuple[str, str, str], ...] = (
    (
        "kopicode",
        "Decided live, no prompt yet",
        "Exact rules, decided for every command. Auto allows everything that is not blocked. "
        "A read-only role can still edit files: kopicode has no way to refuse an edit.",
    ),
    (
        "claude-code",
        "Set for each run",
        "Passed as allow and deny rules for each run. Auto refuses curl and wget outright. "
        "Read-only roles cannot edit files.",
    ),
    (
        "codex",
        "Set for each run, coarse",
        "Only two sandboxes exist: read-only, or edit the folder. Ask first and read-only roles "
        "get the read-only one, and Auto is the same as Standard. Single commands cannot be "
        "filtered.",
    ),
)
