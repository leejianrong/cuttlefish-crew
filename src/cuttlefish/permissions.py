"""Permission modes and per-role access (docs/SLICES.md V4-C, ADR-0025).

A project has one **mode**; a role may override it with an **access** level. The effective
access of a delegation is the role's own, else the project's, else ``standard``.

- ``ask-first``: files can be read and edited; no shell command runs on its own. On kopicode
  inside the fleet daemon a command stops the agent and waits for a person (ADR-0028), and so it
  does on Codex over ``app-server`` (V4-M) and on Claude Code over stream-json (V4-K).
- ``standard``: the built-in dev presets plus anything declared (ADR-0023); on kopicode a
  command off the list is asked about, as in ask-first, on Codex too (over ``app-server``; over
  ``codex exec`` the list is not enforced, it only has a folder sandbox).
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
        "Agents can read and edit files. No command runs on its own. Each command stops the "
        "agent until you allow or deny it.",
    ),
    "standard": (
        "Standard",
        "Everyday dev commands run on their own. Anything off the list stops the agent until "
        "you allow or deny it.",
    ),
    "auto": (
        "Auto",
        "Any command runs without asking, except the always-blocked list (and curl and wget on "
        "Claude Code, which cannot tell a download piped into a shell from a plain one).",
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
        "Asks you live",
        "Every command that prompts is decided as it is asked, like kopicode; what Claude Code "
        "itself treats as read-only (ls) never prompts. Never-allowed prefixes are also denied "
        "to it as tool rules. Its own questions are put to you too. Auto refuses curl and wget "
        "outright. Read-only roles cannot edit files. Over a sandbox provider, or with "
        "CUTTLEFISH_CLAUDE_CODE_TRANSPORT=print, it cannot pause and refuses a command nothing "
        "approves.",
    ),
    (
        "codex",
        "Asks you live",
        "Every command is decided as it is asked, like kopicode, and edits are accepted only "
        "inside the project folder. A command it accepts runs outside Codex's own sandbox, so "
        "the blocked list and your command list are the only guard. Read-only roles get the "
        "read-only sandbox. Its own questions are not asked live yet. Over a sandbox provider, "
        "or with CUTTLEFISH_CODEX_TRANSPORT=exec, it is coarse: two sandboxes, your command "
        "list not enforced, and it cannot pause.",
    ),
)
