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
