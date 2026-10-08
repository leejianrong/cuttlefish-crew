"""Permission modes and per-role access (docs/SLICES.md V4-C, ADR-0025).

A project has one **mode**; a role may override it with an **access** level. The effective
access of a delegation is the role's own, else the project's, else ``standard``.

- ``ask-first``: files can be read and edited; no shell command runs on its own. On kopicode
  inside the fleet daemon a command stops the agent and waits for a person (ADR-0028); on
  Claude Code, which cannot pause, it is refused, and Codex gets a read-only sandbox.
- ``standard``: the built-in dev presets plus anything declared (ADR-0023); on kopicode a
  command off the list is asked about, as in ask-first. Codex does not filter commands (it
  runs in a folder sandbox), so the list is not enforced there.
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
        "Agents can read and edit files. No command runs on its own. With kopicode, each "
        "command stops the agent until you allow or deny it. Claude Code cannot pause, so "
        "there a command is refused. Codex cannot pause either and runs in a read-only "
        "sandbox, so it can read but not edit.",
    ),
    "standard": (
        "Standard",
        "Everyday dev commands run on their own. With kopicode, anything off the list stops "
        "the agent until you allow or deny it. Claude Code refuses it. Codex does not filter "
        "commands: it runs in a sandbox that lets it edit the project folder and /tmp, so a "
        "command off the list can still run (seen live: docker and curl ran).",
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
        "Asks you live",
        "Every command is decided as it is asked. Ask first and Standard pause the agent on a "
        "command that is not allowed and show it under Needs you; an unanswered one is denied "
        "when its time runs out. Auto allows everything that is not blocked. A read-only role "
        "can still edit files: kopicode has no way to refuse an edit.",
    ),
    (
        "claude-code",
        "Set for each run",
        "Passed as allow and deny rules for each run. It cannot pause to ask, so a command "
        "that needs an answer is refused. Auto refuses curl and wget outright. Read-only roles "
        "cannot edit files.",
    ),
    (
        "codex",
        "Set for each run, coarse",
        "Only two sandboxes exist: read-only, or edit the project folder and /tmp. It cannot "
        "pause to ask and does not filter single commands, so Standard and Auto are the same: "
        "your command list is not enforced. A write the sandbox stops just fails inside the "
        "run and the round can still read completed, never refused. Ask first and read-only "
        "roles get the read-only sandbox. Commits can fail (.git is read-only there).",
    ),
)
